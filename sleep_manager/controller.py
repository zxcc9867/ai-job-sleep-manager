"""Application orchestration, with injected OS boundary for safe testing."""
import hashlib
import json
import time
from .engine import Engine, Decision
from .power import PowerActionCancelled

class Controller:
    def __init__(self,store,power,reader,clock=time.monotonic):
        self.store,self.power,self.reader,self.clock=store,power,reader,clock
        settings=store.settings()
        self.engine=Engine(delay=settings.get('delay',600),enabled=settings.get('enabled',True),
                           recovery=settings.get('recovery',1800),action=settings.get('action','sleep'))
        self.started_at=time.time()
        self.observed_now={}
        self.seq=0
        self.generation=0
        self.error=''
        self.shutdown_notice=''
        self.fault_since=None
        self.retry_at=0
        self.last_tick=clock()
        self.status=None
        self.recent_events=[]
        self.live_providers=set()
        self.claude_request_starts={}
        for seq,event in store.recent():
            try:
                self.engine.ingest(event,clock())
            except (ValueError,TypeError):
                continue
            self.seq=seq
            if event.get('provider')=='claude' and event.get('kind')=='start':
                self.claude_request_starts[event['session_id']]=event.get('timestamp',0)
        self.engine.jobs={k:j for k,j in self.engine.jobs.items() if j.active or j.state in ('unknown','waiting')}
        for job in self.engine.jobs.values():
            job.state='unknown'; job.unknown_since=clock()
            job.unknown_children.update({child:clock() for child in job.children})
            job.children.clear(); job.background=False
        self.restored_jobs=set(self.engine.jobs)
        self.engine.seen_work=bool(self.engine.jobs)
        self.engine.baseline=None

    def _accept(self,event):
        if event.get('provider')=='codex' and event.get('session_id') in getattr(self.reader,'excluded_sessions',set()): return
        event=dict(event)
        if event.get('metadata_complete') is False and event.get('kind') in ('session_end','cancelled','failed'):
            event['kind']='unknown'
        previous=self.engine.jobs.get(event.get('provider','')+':'+event.get('session_id',''))
        previous_stamp=max([previous.last_timestamp,*previous.child_timestamps.values()]) if previous else 0
        self.engine.ingest(event,self.clock())
        stamp=event.get('timestamp',0)
        if isinstance(stamp,(int,float)) and self.started_at<=stamp<=time.time()+5 and stamp>=previous_stamp:
            key=event['provider']+':'+event['session_id']
            job=self.engine.jobs[key]
            turn=event.get('turn_id','')
            if not turn or not job.turn_id or turn==job.turn_id:
                self.observed_now[key]=(stamp,event.get('source','새 상태 이벤트'))
        if event.get('provider')=='claude' and event.get('kind')=='start':
            sid=event['session_id']
            self.claude_request_starts[sid]=max(self.claude_request_starts.get(sid,0),event.get('timestamp',0))
        if event.get('source') == 'codex-local-log' and event.get('kind') in ('complete','cancelled'):
            for parent in list(self.engine.jobs.values()):
                child=event['session_id']
                if child in parent.children or child in parent.unknown_children:
                    self.engine.ingest(dict(provider=parent.provider,session_id=parent.session_id,kind='child_stop',task_id=child,metadata_complete=True),self.clock())
        self.generation+=1
        if event.get('kind')!='session_start':
            self.live_providers.add(event['provider'])
        self.recent_events.append((time.strftime('%H:%M:%S'),event.get('provider'),event.get('kind')))
        self.recent_events=self.recent_events[-8:]

    def job_observation(self,job,connections):
        key=job.provider+':'+job.session_id
        event=self.observed_now.get(key)
        connected=connections.get(job.session_id) if job.provider=='claude' else None
        historical=key in self.restored_jobs and job.state=='unknown' and not job.active and not event and not connected
        if connected:connection='현재 세션 연결 확인 · '+connected+' (실행·완료 판단과 별도)'
        elif event:
            connection='이번 실행에서 이벤트 확인 · '+time.strftime('%m/%d %H:%M:%S',time.localtime(event[0]))
        elif key in self.restored_jobs:connection='이전 실행에서 복원 · 현재 세션 연결 미확인'
        else:connection='현재 연결을 확정할 새 이벤트가 필요합니다.'
        stamp=max([job.last_timestamp,*job.child_timestamps.values()])
        last=time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(stamp)) if stamp else '시각 미제공'
        return dict(historical=historical,connection=connection,last_event=last)

    def drain(self):
        # Drain committed hooks first, then authoritative final Codex lifecycle evidence.
        while True:
            rows=self.store.after(self.seq)
            if not rows:
                break
            for seq,event in rows:
                self._accept(event)
                self.seq=seq
            if len(rows)<2000:
                break
        for event in self.reader.poll():
            self._accept(event)
        # Remove previously restored hook state only for positively identified internal sessions.
        removed=False
        for session in getattr(self.reader,'excluded_sessions',set()):
            removed=self.engine.jobs.pop('codex:'+session,None) is not None or removed
        if removed:
            self.engine.baseline=None
            if not self.engine.jobs: self.engine.seen_work=False
            if not any(j.provider=='codex' for j in self.engine.jobs.values()): self.live_providers.discard('codex')
        # Per-file unknown events isolate reader faults from healthy providers.

    def set_presence(self,names):
        # A tool process can remain open after its last job. It is not a job ID.
        for provider,images in [('codex',{'codex.exe'}),('claude',{'claude.exe'})]:
            self.engine.jobs.pop(provider+':unobserved',None)
            present=bool(images & names)
            if not present:
                for job in self.engine.jobs.values():
                    if job.provider==provider and job.active:
                        job.state='unknown'
                        job.unknown_children.update({child:self.clock() for child in job.children})
                        job.children.clear(); job.background=False
                        if job.unknown_since is None:
                            job.unknown_since=self.clock()
        if not self.engine.jobs and not self.live_providers:
            self.engine.seen_work=False
            self.engine.baseline=None

    def tick(self):
        now=self.clock()
        if now-self.last_tick>20:
            self.engine.on_resume(now)
        self.last_tick=now
        try:
            self.drain()
            state=self.engine.tick(now,self.power.input_idle_seconds())
            if self.fault_since is not None and state.sleep_due:
                protected=now-self.fault_since<self.engine.recovery
                if now<self.retry_at:
                    self.power.set_required(protected)
                    self.status=Decision('fault',protected,running=state.running,waiting=state.waiting,unknown=state.unknown)
                    return self.status
            holding=state.required
            if self.fault_since is not None and state.sleep_due:
                holding=holding and now-self.fault_since<self.engine.recovery
            self.power.set_required(holding)
            self.status=state
            if state.sleep_due:
                generation=self.generation
                action=self.engine.action
                shutdown_persisted=False
                shutdown_disarmed=False
                self.power.set_required(False)
                def final_check():
                    nonlocal shutdown_disarmed
                    if hasattr(self.reader,'invalidate_scan'):
                        self.reader.invalidate_scan()
                    self.drain()
                    current=self.engine.tick(self.clock(),self.power.input_idle_seconds())
                    ready=generation==self.generation and current.sleep_due and self.engine.enabled
                    if ready and action=='shutdown':
                        # Persistence already finished; no I/O after the final event drain.
                        self.engine.set_enabled(False,self.clock())
                        shutdown_disarmed=True
                    return ready
                try:
                    if action=='shutdown':
                        # A settings write may block. Finish it BEFORE the last job/input checks.
                        self.save(enabled=False)
                        shutdown_persisted=True
                        self.power.shutdown(before_shutdown=final_check)
                        self.shutdown_notice=('관찰 모드: 정상 종료 요청을 기록했습니다. 실제 종료하지 않았습니다.'
                            if self.power.dry_run else 'Windows에 정상 종료를 요청했습니다. 종료 완료 여부는 확인되지 않았습니다.')
                        self.status=self.engine.tick(self.clock(),self.power.input_idle_seconds())
                    else:
                        self.power.suspend(before_suspend=final_check)
                    self.error='관찰 모드: 절전 요청을 기록했습니다.' if self.power.dry_run and action=='sleep' else ''
                    self.engine.on_resume(self.clock())
                    self.fault_since=None
                except Exception as exc:
                    # A fresh job/input is a normal cancellation, not a fault.
                    current=self.engine.tick(self.clock(),self.power.input_idle_seconds())
                    if action=='shutdown' and shutdown_persisted and (isinstance(exc,PowerActionCancelled) or (self.engine.enabled and not current.sleep_due)):
                        if shutdown_disarmed:
                            self.engine.set_enabled(True,self.clock())
                        self.engine.on_resume(self.clock())
                        self.save()
                        current=self.engine.tick(self.clock(),self.power.input_idle_seconds())
                        self.power.set_required(current.required)
                        self.error=''
                        self.status=current
                        return current
                    if action=='shutdown' and (not self.engine.enabled or current.sleep_due):
                        self.engine.set_enabled(False,self.clock())
                        self.save()
                        self.power.set_required(False)
                        self.error=str(exc)
                        self.status=self.engine.tick(self.clock(),self.power.input_idle_seconds())
                        return self.status
                    if not current.sleep_due:
                        self.power.set_required(current.required)
                        self.status=current
                        return current
                    raise
            else:
                self.fault_since=None
                if state.mode!='paused':
                    self.error=''
            return self.status
        except Exception as exc:
            self.error=str(exc)
            self.retry_at=now+30
            if self.fault_since is None:
                self.fault_since=now
            state=self.engine.tick(now,0 if self.status is None else 10**9)
            required=self.engine.enabled and now-self.fault_since<self.engine.recovery and self.engine.seen_work
            try:
                self.power.set_required(required)
            except OSError:
                pass
            self.status=Decision('fault',required,running=state.running,waiting=state.waiting,unknown=state.unknown)
            return self.status

    def set_execution_mode(self,dry_run):
        if not isinstance(dry_run,bool): raise ValueError('Mode must be a boolean')
        if dry_run==self.power.dry_run: return
        if dry_run:
            # Returning to observation must not depend on a healthy reader.
            self.power.set_dry_run(True)
            idle=0
        else:
            self.drain()
            self.set_presence(self.power.process_names())
            idle=self.power.input_idle_seconds()
        now=self.clock()
        self.engine.on_resume(now)
        state=self.engine.tick(now,idle)
        self.power.set_required(state.required)
        if not dry_run: self.power.set_dry_run(False)
        self.generation+=1
        self.last_tick=now
        self.fault_since=None
        self.retry_at=0
        self.error=''
        self.shutdown_notice=''
        self.status=state
        # No suspend/shutdown call here; the normal loop observes a fresh full delay.

    def set_power_settings(self,seconds,action):
        # Validate both fields and commit once before changing the live policy.
        checked=Engine(delay=seconds,action=action)
        self.store.save_settings(dict(delay=checked.delay,action=checked.action,
            enabled=self.engine.enabled,recovery=self.engine.recovery))
        now=self.clock()
        self.engine.set_delay(checked.delay,now)
        self.engine.set_action(checked.action,now)
        self.fault_since=None
        self.error=''
        self.shutdown_notice=''

    def set_action(self,action):
        self.engine.set_action(action,self.clock())
        self.fault_since=None
        self.error=''
        self.shutdown_notice=''
        self.save()

    def set_enabled(self,enabled):
        self.error=''
        self.shutdown_notice=''
        self.engine.set_enabled(enabled,self.clock())
        self.fault_since=None
        self.save()
        self.power.set_required(False)

    def set_delay(self,seconds):
        self.engine.set_delay(seconds,self.clock())
        self.save()

    def save(self,enabled=None):
        self.store.save_settings(dict(delay=self.engine.delay,enabled=self.engine.enabled if enabled is None else enabled,recovery=self.engine.recovery,action=self.engine.action))

    def close(self):
        self.power.close()






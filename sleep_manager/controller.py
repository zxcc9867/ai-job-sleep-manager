"""Application orchestration, with injected OS boundary for safe testing."""
import hashlib
import json
import os
import sqlite3
import time
import uuid
import subprocess
from . import __version__
from .engine import Engine, Decision
from .power import PowerActionCancelled
from .power_state import PowerActionState, PowerActionFailure, PreflightTransientError, action_message

def current_boot_epoch():
    if os.name != 'nt': return None
    import ctypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetTickCount64.restype=ctypes.c_ulonglong
    return time.time()-kernel.GetTickCount64()/1000

class Controller:
    def __init__(self,store,power,reader,clock=time.monotonic,boot_epoch=None):
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
        self.last_audit_state=None
        self.run_id=uuid.uuid4().hex
        self.power_action=PowerActionState.restore(store.power_action())
        self.retry_generation=None
        self.retry_input_baseline=None
        self.persistence_error=''
        if self.power_action.phase=='deferred':
            self.power_action=PowerActionState(**{**self.power_action.payload(),'retry_at':clock()+30})
        if self.power_action.phase in ('submitting','requested','uncertain','simulated','failed'):
            self.engine.set_enabled(False,clock())
        if not self.engine.enabled:
            if self.power_action.phase=='idle':
                previous=next((e for e in store.power_events(200) if e['code']=='shutdown_failed'),None)
                if previous:
                    self.power_action=PowerActionState('failed','legacy_shutdown_failure',previous['detail'],at=previous['at'])
            self._restore_power_message()
        boot_epoch=current_boot_epoch() if boot_epoch is None else boot_epoch
        previous_boot=store.boot_epoch()
        for seq,event in store.recent():
            try:
                self.engine.ingest(event,clock())
            except (ValueError,TypeError):
                continue
            self.seq=seq
            if event.get('provider')=='claude' and event.get('kind')=='start':
                self.claude_request_starts[event['session_id']]=event.get('timestamp',0)
        rebooted=(boot_epoch is not None and previous_boot is not None
                  and abs(boot_epoch-previous_boot)>120)
        interrupted_count=0
        if rebooted:
            for job in list(self.engine.jobs.values()):
                stamp=max([job.last_timestamp,*job.child_timestamps.values()])
                if job.state not in ('complete','idle','interrupted') and 0<stamp<boot_epoch:
                    event=dict(provider=job.provider,session_id=job.session_id,
                               kind='interrupted',timestamp=boot_epoch,source='system-reboot-detected')
                    store.append(event)
                    self.engine.ingest(event,clock())
                    interrupted_count+=1
            self._audit('reboot_detected','Windows 재부팅 감지 · 원인은 별도 확인 필요')
            if interrupted_count:
                self._audit('reboot_interrupted',f'이전 AI 실행 {interrupted_count}개 중단 · 전원 전환 보류')
        if boot_epoch is not None: store.save_boot_epoch(boot_epoch)
        self.engine.jobs={k:j for k,j in self.engine.jobs.items() if j.active or j.state in ('unknown','waiting','interrupted')}
        for job in self.engine.jobs.values():
            if job.state=='interrupted': continue
            job.state='unknown'; job.unknown_since=clock()
            job.unknown_children.update({child:clock() for child in job.children})
            job.children.clear(); job.background=False
        self.restored_jobs=set(self.engine.jobs)
        self.engine.seen_work=bool(self.engine.jobs)
        self.engine.baseline=None

    def _audit(self,code,detail):
        try: self.store.record_power_event(code,detail)
        except (OSError,sqlite3.Error): pass

    def _audit_decision(self,state):
        signature=(state.mode,state.running,state.waiting,state.unknown,state.interrupted,self.power.dry_run)
        if signature!=self.last_audit_state:
            self.last_audit_state=signature
            self._audit('decision',f'상태 {state.mode} · 실행 {state.running} · 입력 대기 {state.waiting} · 확인 필요 {state.unknown} · 재부팅 중단 {state.interrupted} · 실행 모드 {"관찰" if self.power.dry_run else "실제"}')

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
        historical=key in self.restored_jobs and job.state in ('unknown','interrupted') and not job.active and not event and not connected
        if connected:connection='현재 세션 연결 확인 · '+connected+' (실행·완료 판단과 별도)'
        elif event:
            connection='이번 실행에서 이벤트 확인 · '+time.strftime('%m/%d %H:%M:%S',time.localtime(event[0]))
        elif key in self.restored_jobs:connection='이전 실행에서 복원 · 현재 세션 연결 미확인'
        else:connection='현재 연결을 확정할 새 이벤트가 필요합니다.'
        stamp=max([job.last_timestamp,*job.child_timestamps.values()])
        last=time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(stamp)) if stamp else '시각 미제공'
        return dict(historical=historical,connection=connection,last_event=last)

    def drain(self,poll_reader=True):
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
        if poll_reader:
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

    def _restore_power_message(self):
        message=action_message(self.power_action,self.clock())
        self.error=(message+' '+self.power_action.message).strip() if self.power_action.phase in ('failed','uncertain','submitting') else ''
        self.shutdown_notice=message if self.power_action.phase in ('requested','simulated') else ''

    def _settings_payload(self,enabled):
        return dict(delay=self.engine.delay,enabled=enabled,recovery=self.engine.recovery,action=self.engine.action)

    def _set_power_action(self,state,settings=None,strict=False):
        try:
            self.store.save_power_action(state.payload(),settings=settings)
            self.persistence_error=''
        except (OSError,sqlite3.Error,PreflightTransientError) as exc:
            self.persistence_error=type(exc).__name__
            if strict: raise
        self.power_action=state
        self._restore_power_message()

    def _reset_preflight(self):
        self.retry_generation=None
        self.retry_input_baseline=None
        self.fault_since=None
        self.retry_at=0
        if self.engine.enabled:
            self._set_power_action(PowerActionState(at=time.time(),run_id=self.run_id))

    def _shutdown_fault(self,exc,now,request_started=False):
        transient=isinstance(exc,(PreflightTransientError,TimeoutError,subprocess.TimeoutExpired))
        attempts=self.power_action.attempts
        if transient and not request_started:
            attempts+=1
            if attempts<10:
                reason=getattr(exc,'reason','query_timeout')
                self.retry_generation=self.generation
                try: self.retry_input_baseline=now-self.power.input_idle_seconds()
                except Exception: self.retry_input_baseline=None
                self._set_power_action(PowerActionState('deferred',reason,attempts=attempts,
                    retry_at=now+30,at=time.time(),run_id=self.run_id),settings=self._settings_payload(True))
                self._audit('shutdown_deferred',f'요청 전 재확인 {attempts}/10 · {reason}')
                self.power.set_required(self.engine.seen_work)
                known=self.engine.tick(now,10**9)
                self.status=Decision('fault',self.engine.seen_work,running=known.running,
                    waiting=known.waiting,unknown=known.unknown,interrupted=known.interrupted)
                return self.status
        reason=('retry_limit' if transient and attempts>=10 else
                getattr(exc,'reason','storage' if isinstance(exc,(sqlite3.Error,OSError)) else 'unknown_error'))
        state=PowerActionState('uncertain' if request_started else 'failed',reason,str(exc)[:240],
            attempts=attempts,request_started=request_started,at=time.time(),run_id=self.run_id)
        self.engine.set_enabled(False,now)
        self._set_power_action(state,settings=self._settings_payload(False))
        self._audit('shutdown_failed',f'{state.phase} · {reason} · {type(exc).__name__}')
        self.power.set_required(False)
        self.status=self.engine.tick(now,0)
        return self.status

    def _attempt_shutdown(self,state,now):
        generation=self.generation
        disarmed=False
        intent=PowerActionState('submitting',attempts=self.power_action.attempts,at=time.time(),run_id=self.run_id)
        def final_check():
            nonlocal disarmed
            if hasattr(self.reader,'invalidate_scan'): self.reader.invalidate_scan()
            self.drain()
            current=self.engine.tick(self.clock(),self.power.input_idle_seconds())
            ready=(generation==self.generation and current.sleep_due and self.engine.enabled
                   and self.engine.action=='shutdown')
            if ready:
                self.engine.set_enabled(False,self.clock())
                disarmed=True
            return ready
        try:
            if hasattr(self.power,'submission_started'): self.power.submission_started=False
            self.power.set_required(False)
            # All persistent writes precede the final drain/input check.
            self._set_power_action(intent,settings=self._settings_payload(False),strict=True)
            self.power.shutdown(before_shutdown=final_check)
            phase='simulated' if self.power.dry_run else 'requested'
            # A successful injected adapter must also be treated as one-shot.
            self.engine.set_enabled(False,self.clock())
            self._set_power_action(PowerActionState(phase,request_started=not self.power.dry_run,
                at=time.time(),run_id=self.run_id),settings=self._settings_payload(False))
            self._audit('shutdown_simulated' if self.power.dry_run else 'shutdown_requested',self.shutdown_notice)
            self.status=self.engine.tick(self.clock(),0)
            return self.status
        except Exception as exc:
            started=getattr(exc,'request_started',False) or getattr(self.power,'submission_started',False)
            # PowerActionCancelled always precedes the native call, including input
            # received after the final callback has disarmed the in-memory engine.
            if isinstance(exc,PowerActionCancelled): started=False
            current=self.engine.tick(self.clock(),self.power.input_idle_seconds())
            if not started and (isinstance(exc,PowerActionCancelled) or (not disarmed and not current.sleep_due)):
                if disarmed: self.engine.set_enabled(True,self.clock())
                self.engine.on_resume(self.clock())
                self._set_power_action(PowerActionState(at=time.time(),run_id=self.run_id),
                    settings=self._settings_payload(self.engine.enabled),strict=True)
                self._reset_preflight()
                self.status=self.engine.tick(self.clock(),self.power.input_idle_seconds())
                self.power.set_required(self.status.required)
                return self.status
            # Unknown adapter errors after the final callback are conservatively
            # considered potentially submitted. Never repeat an ambiguous request.
            started=started or (disarmed and not isinstance(exc,(PowerActionCancelled,PreflightTransientError,TimeoutError,subprocess.TimeoutExpired)))
            return self._shutdown_fault(exc,now,request_started=started)

    def diagnostics(self):
        result=self.power.diagnostics()
        permission=getattr(self.power,'shutdown_permission',lambda:{'available':None,'error_code':None})()
        result.update(run_id=self.run_id,version=__version__,pid=os.getpid(),
            database_path=str(self.store.path.resolve()),data_directory=str(self.store.root.resolve()),
            settings=self.store.settings(),management_enabled=self.engine.enabled,
            power_action=self.power_action.payload(),persisted_power_action=self.store.power_action(),
            shutdown_permission=permission,persistence_error=self.persistence_error,
            completion_action=self.engine.action,last_error=self.error,shutdown_notice=self.shutdown_notice)
        return result

    def tick(self):
        now=self.clock()
        if now-self.last_tick>20:
            self.engine.on_resume(now)
        self.last_tick=now
        try:
            shutdown=self.engine.action=='shutdown'
            if shutdown and self.power_action.phase=='deferred' and self.engine.enabled:
                self.drain(poll_reader=False)
                idle=self.power.input_idle_seconds()
                fresh_input=self.retry_input_baseline is not None and now-idle>self.retry_input_baseline+.1
                if self.generation!=self.retry_generation or fresh_input:
                    self.engine.on_resume(now)
                    self._reset_preflight()
                elif now<self.power_action.retry_at:
                    current=self.engine.tick(now,idle)
                    self.power.set_required(current.required)
                    self.status=Decision('fault',current.required,running=current.running,
                        waiting=current.waiting,unknown=current.unknown,interrupted=current.interrupted)
                    return self.status
            self.drain()
            state=self.engine.tick(now,self.power.input_idle_seconds())
            self._audit_decision(state)
            if shutdown:
                self.status=state
                self.power.set_required(state.required)
                if state.sleep_due: return self._attempt_shutdown(state,now)
                if self.power_action.phase=='deferred' and state.mode in ('running','waiting','unknown','interrupted'):
                    self._reset_preflight()
                return state
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
                self.power.set_required(False)
                def final_check():
                    if hasattr(self.reader,'invalidate_scan'):
                        self.reader.invalidate_scan()
                    self.drain()
                    current=self.engine.tick(self.clock(),self.power.input_idle_seconds())
                    return (generation==self.generation and current.sleep_due and self.engine.enabled
                            and self.engine.action=='sleep')
                try:
                    self.power.suspend(before_suspend=final_check)
                    self.error='관찰 모드: 절전 요청을 기록했습니다.' if self.power.dry_run else ''
                    self.engine.on_resume(self.clock())
                    self.fault_since=None
                except Exception as exc:
                    # A fresh job/input is a normal cancellation, not a fault.
                    current=self.engine.tick(self.clock(),self.power.input_idle_seconds())
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
            if self.engine.action=='shutdown':
                if not self.engine.enabled:
                    self._restore_power_message()
                    self.status=self.engine.tick(now,0)
                    self.power.set_required(False)
                    return self.status
                return self._shutdown_fault(exc,now)
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
        if dry_run==self.power.dry_run:
            self.store.save_execution_dry_run(dry_run)
            return
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
        try:
            self.store.save_execution_dry_run(dry_run)
        except (OSError,sqlite3.Error):
            if not dry_run:
                try:
                    self.power.set_dry_run(True)
                except Exception as rollback_error:
                    self.engine.set_enabled(False,self.clock())
                    self.error='모드 저장·안전 복귀 실패: '+str(rollback_error)
                    raise RuntimeError(self.error) from rollback_error
                self.engine.on_resume(self.clock())
                self.status=self.engine.tick(self.clock(),0)
            raise
        self._audit('mode_changed','관찰 모드 적용' if dry_run else '실제 전원 제어 적용')
        self._reset_preflight()
        self._restore_power_message()
        # No suspend/shutdown call here; the normal loop observes a fresh full delay.

    def confirm_job_finished(self,key):
        self.drain()
        job=self.engine.jobs.get(key)
        if not job or (job.state not in ('unknown','interrupted') and not job.unknown_children) or job.active:
            raise ValueError('현재 실행 중이거나 확인 대상이 아닌 작업입니다.')
        stamp=max([time.time(),job.last_timestamp,*job.child_timestamps.values()])+0.001
        self.store.append(dict(provider=job.provider,session_id=job.session_id,kind='complete',
                               timestamp=stamp,source='user-confirmed',metadata_complete=True,
                               background_active=False))
        self.drain()
        self.engine.on_resume(self.clock())
        self._audit('user_confirmed',f'{job.provider} 작업 1개 실행 종료를 사용자 직접 확인')

    def set_power_settings(self,seconds,action):
        # Validate both fields and commit once before changing the live policy.
        checked=Engine(delay=seconds,action=action)
        self.store.save_settings(dict(delay=checked.delay,action=checked.action,
            enabled=self.engine.enabled,recovery=self.engine.recovery))
        now=self.clock()
        self.engine.set_delay(checked.delay,now)
        self.engine.set_action(checked.action,now)
        self.generation+=1
        self.fault_since=None
        self._reset_preflight()
        self._restore_power_message()

    def set_action(self,action):
        self.engine.set_action(action,self.clock())
        self.generation+=1
        self.fault_since=None
        self.save()
        self._reset_preflight()
        self._restore_power_message()

    def set_enabled(self,enabled):
        state=PowerActionState('idle' if enabled else 'paused','' if enabled else 'user_pause',
            at=time.time(),run_id=self.run_id)
        self._set_power_action(state,settings=self._settings_payload(bool(enabled)),strict=True)
        self.engine.set_enabled(enabled,self.clock())
        self.generation+=1
        self.fault_since=None
        self.retry_generation=None
        self.retry_input_baseline=None
        self.retry_at=0
        self.power.set_required(False)

    def set_delay(self,seconds):
        self.engine.set_delay(seconds,self.clock())
        self.generation+=1
        self.save()
        self._reset_preflight()

    def save(self,enabled=None):
        self.store.save_settings(dict(delay=self.engine.delay,enabled=self.engine.enabled if enabled is None else enabled,recovery=self.engine.recovery,action=self.engine.action))

    def close(self):
        self.power.close()

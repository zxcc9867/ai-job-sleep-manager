"""Version-dependent Codex lifecycle observer. Never exports transcript contents."""
from datetime import datetime
import json
from pathlib import Path
import re
import time
from .job_view import observe_details
from .activity_view import reset_activity
from .request_history import recover_request

UUID=re.compile(r'([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})',re.I)
TERMINAL={'complete','failed','cancelled'}
MAX_READ=4*1024*1024
MAX_RECOVERY=32*1024*1024

def parse_record(record, session):
    payload=record.get('payload',{})
    if not isinstance(payload,dict):
        return None
    tag=record.get('type')
    kind=None
    if tag=='event_msg':
        kind={'task_started':'start','task_complete':'complete','turn_aborted':'cancelled'}.get(payload.get('type'))
    elif tag=='response_item' and payload.get('type')=='function_call':
        if payload.get('name') in ('request_user_input','functions.request_user_input'):
            kind='waiting'
    if not kind:
        return None
    stamp=record.get('timestamp')
    try:
        timestamp=datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
    except (AttributeError,ValueError):
        timestamp=0
    return dict(provider='codex',session_id=session,kind=kind,turn_id=payload.get('turn_id',''),task_id='',timestamp=timestamp,metadata_complete=True,source='codex-local-log')

def recover_lifecycle(path,end,session):
    """Recover explicit lifecycle evidence, independently of display text.

    Stop at the nearest start/terminal boundary, validate newer turn contexts,
    then replay only input-wait calls and their matching results. Malformed or
    out-of-budget history remains unknown. No message body is evidence.
    """
    cursor=end;floor=max(0,end-MAX_RECOVERY);carry=b'';later=[];context=''
    try:
        with path.open('rb') as stream:
            while cursor>floor:
                begin=max(floor,cursor-1024*1024)
                stream.seek(begin);data=stream.read(cursor-begin)+carry;cursor=begin
                lines=data.split(b'\n');carry=lines.pop(0)
                if cursor==0:lines.insert(0,carry);carry=b''
                for raw in reversed(lines):
                    if not raw:continue
                    record=json.loads(raw)
                    if not isinstance(record,dict):return None
                    payload=record.get('payload',{})
                    if not isinstance(payload,dict):continue
                    tag=record.get('type');kind=payload.get('type')
                    if tag=='turn_context':
                        turn=payload.get('turn_id','')
                        if not isinstance(turn,str) or not turn:return None
                        if context and context!=turn:return None
                        context=turn
                    if tag=='event_msg' and kind in ('task_started','task_complete','turn_aborted'):
                        boundary=parse_record(record,session);turn=payload.get('turn_id','')
                        if not isinstance(turn,str) or not turn or len(turn)>256 or (context and context!=turn):return None
                        if not boundary or not boundary['timestamp']:return None
                        if boundary['kind'] in TERMINAL:
                            # A later input call/result after a terminal is ambiguous.
                            if later:return None
                            return dict(events=[boundary],turn=turn,pending={})
                        pending={};events=[boundary]
                        for item in reversed(later):
                            data=item['payload'];call=data.get('call_id')
                            if not isinstance(call,str) or not call:return None
                            if data['type']=='function_call':
                                pending[(session,call)]=turn
                                event=parse_record(item,session)
                                event['turn_id']=turn;events.append(event)
                            elif (session,call) in pending:
                                pending.pop((session,call))
                                try:stamp=datetime.fromisoformat(item['timestamp'].replace('Z','+00:00')).timestamp()
                                except (ValueError,AttributeError,KeyError):return None
                                events.append(dict(provider='codex',session_id=session,kind='waiting' if pending else 'running',turn_id=turn,timestamp=stamp,metadata_complete=True,source='codex-local-log'))
                        return dict(events=events,turn=turn,pending=pending)
                    if tag=='response_item' and (kind=='function_call_output' or (kind=='function_call' and payload.get('name') in ('request_user_input','functions.request_user_input'))):
                        later.append(dict(type=tag,timestamp=record.get('timestamp'),payload={k:payload.get(k) for k in ('type','name','call_id')}))
                if len(carry)>MAX_RECOVERY:return None
    except (OSError,ValueError,TypeError,RecursionError):pass
    return None


class CodexLogs:
    def __init__(self, root):
        self.root=Path(root)
        self.files={}
        self.started=time.time()
        self.last_scan=0
        self.last_error=''
        self.pending={}
        self.details={}
        self.excluded_sessions=set()

    def invalidate_scan(self):
        self.last_scan=0

    def poll(self):
        events=[]
        self.last_error=''
        now=time.time()
        if now-self.last_scan>=5 or not self.files:
            self.last_scan=now
            try:
                for p in self.root.rglob('rollout-*.jsonl'):
                    if p in self.files:
                        continue
                    if p.stat().st_mtime < now-86400*2:
                        continue
                    match=UUID.search(p.name)
                    if match:
                        self.files[p]=dict(offset=0,session=match.group(1),first=True,turn='',internal=None)
            except OSError:
                self.last_error='Codex 실행 기록을 읽을 수 없습니다.'
        for path, state in list(self.files.items()):
            session=state['session']
            if state.get('internal') is None:
                state['internal']=self.internal_session(path,session)
            if state.get('internal'):
                self.excluded_sessions.add(session)
                self.details.pop(session,None)
                continue
            try:
                size=path.stat().st_size
                if size==state['offset']:
                    continue
                if size<state['offset']:
                    self.clear_details(session)
                    state['offset']=0
                    state['first']=False
                    state.pop('request_recovery_turn',None)
                    events.append(self.unknown(session))
                uncertain=False
                with path.open('rb') as f:
                    if size-state['offset']>MAX_READ:
                        self.clear_details(session)
                        f.seek(size-MAX_READ)
                        f.readline()
                        uncertain=True
                    else:
                        f.seek(state['offset'])
                    chunk=f.read(MAX_READ)
                    end=chunk.rfind(b'\n')
                    if end<0:
                        continue
                    consumed=chunk[:end+1]
                    state['offset']=f.tell()-len(chunk)+end+1
                if uncertain: self.clear_details(session)
                batch=[self.unknown(session)] if uncertain else []
                for raw in consumed.splitlines():
                    try:
                        record=json.loads(raw)
                        try:
                            observe_details(self.details.setdefault(session,{}),record)
                        except Exception:
                            # Display failures must never consume or suppress lifecycle events.
                            self.clear_details(session)
                        event=parse_record(record,session)
                        if event and event['kind'] in TERMINAL | {'start'}:
                            self.pending={key:value for key,value in self.pending.items() if key[0]!=session}
                            if event['kind']=='start':state['turn']=event.get('turn_id','')
                        payload=record.get('payload',{})
                        if record.get('type')=='response_item' and isinstance(payload,dict):
                            call_id=payload.get('call_id')
                            if event and event['kind']=='waiting' and call_id:
                                event['turn_id']=state['turn']
                                self.pending[(session,call_id)]=state['turn']
                            if payload.get('type')=='function_call_output' and (session,call_id) in self.pending:
                                input_turn=self.pending.pop((session,call_id))
                                waiting=any(key[0]==session and turn==input_turn for key,turn in self.pending.items())
                                event=dict(provider='codex',session_id=session,kind='waiting' if waiting else 'running',turn_id=input_turn,timestamp=0,metadata_complete=True,source='codex-local-log')
                        if event:
                            batch.append(event)
                    except (ValueError,TypeError,AttributeError,RecursionError):
                        self.clear_details(session)
                        batch.append(self.unknown(session))
                recovered=recover_lifecycle(path,state['offset'],session) if uncertain else None
                if recovered is not None:
                    batch=recovered['events']
                    state['turn']=recovered['turn']
                    self.pending={key:value for key,value in self.pending.items() if key[0]!=session}
                    self.pending.update(recovered['pending'])
                info=self.details.setdefault(session,{})
                turn=info.get('activity_turn','')
                if not info.get('latest_request') and (uncertain or state['first'] or state.get('request_recovery_turn')!=turn):
                    # A request may precede the lifecycle tail by many megabytes.
                    # Restore only a request proven to belong to the latest turn.
                    info.update(recover_request(path,state['offset'],expected_turn=turn))
                    state['request_recovery_turn']=turn
                if state['first']:
                    state['first']=False
                    # Opening the manager must not arm sleep for already finished history.
                    if batch:
                        last=batch[-1]
                        if last['kind'] not in TERMINAL or last.get('timestamp',0)>=self.started:
                            if recovered is not None and last['kind'] not in TERMINAL:
                                events.extend(batch)
                            else:events.append(last)
                else:
                    events.extend(batch)
            except OSError:
                self.clear_details(session)
                self.last_error='Codex 실행 기록 연결이 끊겼습니다.'
                events.append(self.unknown(session))
        return events

    @staticmethod
    def internal_session(path,session):
        """Identify internal approval reviews before reading large transcript tails."""
        try:
            with path.open('rb') as stream: raw=stream.readline(1024*1024)
            if not raw.endswith(b'\n'): return None
            record=json.loads(raw)
            if not isinstance(record,dict) or record.get('type')!='session_meta': return False
            meta=record.get('payload')
            if not isinstance(meta,dict) or meta.get('id')!=session: return False
            source=meta.get('source')
            if isinstance(source,str):
                if not source.lstrip().startswith('{'): return False
                source=json.loads(source)
            if not isinstance(source,dict): return False
            subagent=source.get('subagent')
            return isinstance(subagent,dict) and subagent.get('other')=='guardian'
        except (OSError,ValueError,TypeError,RecursionError): return None

    def clear_details(self,session):
        info=self.details.setdefault(session,{})
        info['plan']=[]
        reset_activity(info)

    @staticmethod
    def unknown(session):
        return dict(provider='codex',session_id=session,kind='unknown',metadata_complete=False,source='codex-local-log',timestamp=0)




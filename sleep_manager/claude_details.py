"""Read-only Claude display details for sessions already known through hooks.

Transcript text is ephemeral and never produces lifecycle events. Unknown schemas,
missing user boundaries and unreadable files must not retain an older instruction.
"""
from datetime import datetime
import json
from pathlib import Path
import time
from .activity_view import text, user_message, update_activity

MAX_READ=32*1024*1024
MAX_TOTAL_READ=64*1024*1024
TOOLS={'Bash':'터미널 명령 실행 중','Read':'파일 읽는 중','Glob':'파일 찾는 중',
       'Grep':'코드 검색 중','Edit':'파일 수정 중','Write':'파일 작성 중',
       'Agent':'하위 작업 요청 중','Task':'하위 작업 요청 중','TodoWrite':'작업 계획 갱신 중',
       'WebFetch':'웹 자료 확인 중','WebSearch':'웹 검색 중','AskUserQuestion':'사용자 답변 요청 중'}
INTERNAL=('<local-command','<command-name>','<command-message>','<task-notification>',
          '<system-reminder>','This session is being continued from a previous conversation')


def _stamp(value):
    try:return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    except (ValueError,TypeError,AttributeError,OverflowError):return 0


def read_details(path,session):
    info={};calls={}
    with path.open('rb') as stream:
        end=path.stat().st_size; offset=max(0,end-MAX_READ);stream.seek(offset)
        if offset:stream.readline()
        raw=stream.read(min(MAX_READ,end-stream.tell()))
    # An unfinished record is retried after the writer completes it.
    lines=raw.split(b'\n')[:-1]
    for line in lines:
        if not line.strip():continue
        try:record=json.loads(line)
        except (ValueError,RecursionError):
            info={};calls={};continue
        if not isinstance(record,dict) or record.get('sessionId')!=session:continue
        if record.get('isSidechain') or record.get('isMeta') or record.get('isCompactSummary'):continue
        if record.get('type')=='system' and record.get('subtype')=='compact_boundary':
            info={};calls={};continue
        message=record.get('message')
        if not isinstance(message,dict):continue
        kind=record.get('type');content=message.get('content');stamp=record.get('timestamp','')
        if kind=='user' and message.get('role')=='user':
            blocks=[dict(type='text',text=content)] if isinstance(content,str) else content
            if not isinstance(blocks,list):continue
            results=[b for b in blocks if isinstance(b,dict) and b.get('type')=='tool_result']
            if results:
                for block in results:
                    call=block.get('tool_use_id')
                    if isinstance(call,str) and call in calls:
                        calls.pop(call)
                        update_activity(info,next(reversed(calls.values())) if calls else '도구 응답 수신 · 다음 활동 확인 중',stamp)
                continue
            public=[]
            for block in blocks:
                if not isinstance(block,dict):continue
                if block.get('type')=='text':
                    value=block.get('text','')
                    if isinstance(value,str) and not value.lstrip().startswith(INTERNAL):
                        public.append(dict(type='input_text',text=value))
                elif block.get('type')=='image':public.append(dict(type='input_image'))
            request,_=user_message(public)
            if not request:continue
            info=dict(latest_request=request,request_at=text(stamp,64));calls={}
            update_activity(info,'최신 지시 수신 · 실행 기록 확인 중',stamp)
        elif kind=='assistant' and message.get('role')=='assistant' and info.get('latest_request'):
            if not isinstance(content,list):continue
            for block in content[:64]:
                if not isinstance(block,dict):continue
                if block.get('type')=='text':
                    value=text(block.get('text'))
                    if value:
                        info['commentary']=value;info['commentary_at']=text(stamp,64)
                        update_activity(info,'AI가 진행 상황을 알렸습니다',stamp)
                elif block.get('type')=='tool_use':
                    name=block.get('name');call=block.get('id')
                    if not isinstance(name,str):continue
                    label=TOOLS.get(name,'도구 실행 중')
                    if isinstance(call,str) and len(call)<=256:
                        if len(calls)>=64:calls.pop(next(iter(calls)))
                        calls[call]=label
                    update_activity(info,label,stamp)
    return info if info.get('latest_request') else {}


class ClaudeDetails:
    def __init__(self,root,clock=time.monotonic):
        self.root=Path(root).resolve();self.clock=clock
        self.paths={};self.cache={};self.signatures={};self.next_index=0;self.next_read=0
        self.keys=set();self.result={};self.last_error=''

    def lookup(self,jobs,request_starts=None):
        ids={j.session_id for j in jobs if j.provider=='claude' and j.session_id!='unobserved'}
        now=self.clock()
        if now>=self.next_index or ids!=self.keys:
            self.next_index=now+10;self.keys=ids;paths={}
            try:
                for path in self.root.glob('*/*.jsonl'):
                    if path.stem not in ids:continue
                    resolved=path.resolve()
                    if not resolved.is_relative_to(self.root):continue
                    # Duplicate session identities are ambiguous; never pick arbitrarily.
                    if path.stem in paths:paths[path.stem]=None
                    else:paths[path.stem]=path
                self.paths=paths
            except OSError:
                self.paths={};self.last_error='Claude 로컬 기록 목록을 읽을 수 없습니다.'
            self.next_read=0
        if now>=self.next_read:
            self.next_read=now+2;budget=MAX_TOTAL_READ;self.last_error=''
            for sid in list(self.cache):
                if sid not in ids:self.cache.pop(sid,None);self.signatures.pop(sid,None)
            for sid in sorted(ids):
                path=self.paths.get(sid)
                if path is None:
                    self.cache.pop(sid,None);self.signatures.pop(sid,None);continue
                try:
                    stat=path.stat();signature=(str(path),stat.st_mtime_ns,stat.st_size)
                    if self.signatures.get(sid)==signature:continue
                    self.cache.pop(sid,None)
                    size=min(stat.st_size,MAX_READ)
                    if size>budget:continue
                    budget-=size
                    self.cache[sid]=read_details(path,sid);self.signatures[sid]=signature
                except (OSError,ValueError,RecursionError):
                    self.cache.pop(sid,None);self.signatures.pop(sid,None)
                    self.last_error='Claude 로컬 기록을 읽을 수 없습니다. 원래 앱을 확인하세요.'
        starts=request_starts or {};result={}
        for sid in ids:
            info=self.cache.get(sid,{})
            # Prompt hooks and transcript writes can differ slightly in timestamp.
            if info and _stamp(info.get('request_at'))+2>=starts.get(sid,0):
                result['claude:'+sid]=dict(info)
        self.result=result
        return result

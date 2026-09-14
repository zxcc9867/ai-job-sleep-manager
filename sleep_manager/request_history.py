"""Bounded, display-only recovery of the latest request within a proven turn."""
import json
from .activity_view import user_message,is_context,text

MAX_HISTORY=32*1024*1024
BLOCK=1024*1024
MAX_RECORD=MAX_HISTORY


def recover_request(path,end,expected_turn='',max_bytes=MAX_HISTORY):
    # Work backwards without loading a whole transcript or replaying lifecycle events.
    candidate={}; carry=b''; cursor=end; floor=max(0,end-max_bytes)
    try:
        with path.open('rb') as stream:
            while cursor>floor:
                start=max(floor,cursor-BLOCK)
                stream.seek(start)
                data=stream.read(cursor-start)+carry
                cursor=start
                lines=data.split(b'\n'); carry=lines.pop(0)
                if cursor==0: lines.insert(0,carry); carry=b''
                for raw in reversed(lines):
                    if not raw: continue
                    if len(raw)>MAX_RECORD: return {}
                    record=json.loads(raw)
                    if not isinstance(record,dict): return {}
                    payload=record.get('payload',{})
                    if not isinstance(payload,dict): continue
                    tag=record.get('type'); kind=payload.get('type')
                    if tag=='event_msg' and kind=='task_started':
                        turn=text(payload.get('turn_id'),128)
                        if not turn or (expected_turn and turn!=expected_turn): return {}
                        return dict(candidate,request_turn=turn) if candidate else {}
                    if candidate: continue
                    content=None
                    if tag=='response_item' and kind=='message' and payload.get('role')=='user': content=payload.get('content')
                    elif tag=='event_msg' and kind=='user_message':
                        content=[dict(type='input_text',text=payload.get('message',''))]
                        if payload.get('images'): content.append(dict(type='input_image'))
                    if content is not None:
                        request,_=user_message(content)
                        if request and not is_context(request):
                            candidate=dict(latest_request=request,request_at=text(record.get('timestamp'),64))
                if len(carry)>MAX_RECORD: return {}
    except (OSError,ValueError,TypeError,RecursionError): pass
    return {}

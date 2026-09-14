"""Ephemeral latest-turn activity, based only on user-visible messages and tool events."""
from datetime import datetime
import re
import time

FIELDS=('latest_request','request_at','commentary','commentary_at','activity','activity_at','activity_history','activity_calls','activity_turn')


def text(value,limit=500):
    from .job_view import clean_text
    return clean_text(value,limit)


def reset_activity(info):
    for key in FIELDS: info.pop(key,None)


def message_text(content,kind):
    if not isinstance(content,list): return ''
    return text(' '.join(item.get('text','')[:4000] for item in content[:20]
                         if isinstance(item,dict) and item.get('type')==kind and isinstance(item.get('text'),str)))


def user_message(content):
    """Separate Codex's attachment envelope before truncating the user's text."""
    if not isinstance(content,list): return '',False
    blocks=content[:64]
    image=any(isinstance(item,dict) and item.get('type') in ('input_image','image') for item in blocks)
    parts=[]; available=65536
    for item in blocks:
        if not isinstance(item,dict) or item.get('type')!='input_text' or not isinstance(item.get('text'),str): continue
        part=item['text'][:available]; parts.append(part); available-=len(part)
        if not available: break
    raw='\n'.join(parts)
    envelope=raw.lstrip().startswith('# Files mentioned by the user:')
    markup=r'<image\s+(?:name|path)=[^>]*>.*?</image\s*>'
    image=image or bool(re.search(markup,raw,re.S))
    if envelope:
        request=re.search(r'^## My request(?: for Codex)?:[ \t]*(?:\r?\n|$)',raw,re.M)
        if not request: return '파일 첨부 · 텍스트 지시 확인 불가',True
        raw=raw[request.end():]
    raw=re.sub(markup,'',raw,flags=re.S)
    request=text(raw)
    if request: return request,False
    if image: return '이미지 첨부 · 텍스트 지시 없음',True
    if envelope: return '파일 첨부 · 텍스트 지시 없음',True
    return '',False


def is_context(value):
    return value.lstrip().startswith(('# AGENTS.md instructions','<environment_context>','<permissions instructions>',
        '<skills_instructions>','<recommended_plugins>','<turn_aborted>','<subagent_notification>',
        'The following is the Codex agent history whose request action you are assessing.',
        'The following is the Codex agent history added since your last approval assessment.'))


def update_activity(info,label,stamp):
    info['activity']=label; info['activity_at']=text(stamp,64)
    history=info.setdefault('activity_history',[])
    if not history or history[-1]['text']!=label:
        history.append(dict(text=label,at=text(stamp,64)))
        del history[:-6]


def tool_label(name,payload):
    name=name.rsplit('.',1)[-1]
    labels={'exec_command':'터미널 명령 실행 중','write_stdin':'실행 중인 명령 결과 확인 중',
        'apply_patch':'파일 수정 도구 실행 중','update_plan':'작업 계획 갱신 중',
        'spawn_agent':'하위 작업 요청 중','wait_agent':'하위 작업 응답 대기 중',
        'wait_threads':'다른 작업 완료 대기 중','request_user_input':'사용자 답변 요청 중',
        'request_user_input_async':'사용자에게 질문 전달 중','web__run':'자료 조회 도구 실행 중',
        'read_thread':'다른 작업 진행 상황 조회 중','wait':'실행 결과 대기 중'}
    if name in labels: return labels[name]
    if name=='exec':
        code=payload.get('input','')
        if isinstance(code,str) and len(code)<=65536:
            names=re.findall(r'await\s+tools\.([A-Za-z0-9_]+)\s*\(',code)
            kinds={labels[n] for n in names if n in labels}
            if len(kinds)==1: return next(iter(kinds))
        return '도구 묶음 실행 중'
    return '도구 실행 중'


def observe_activity(info,record):
    payload=record.get('payload')
    if not isinstance(payload,dict): return
    kind=payload.get('type'); tag=record.get('type'); stamp=record.get('timestamp','')
    if tag=='event_msg' and kind=='task_started':
        reset_activity(info); info['activity_turn']=text(payload.get('turn_id'),128)
        update_activity(info,'새 지시 처리 준비 중',stamp)
        return
    if tag=='event_msg' and kind in ('task_complete','turn_aborted'):
        info['activity_calls']={}
        update_activity(info,'실행 종료 확인' if kind=='task_complete' else '작업 중단 확인',stamp)
        return
    user=''; attachment_only=False
    if tag=='event_msg' and kind=='user_message':
        content=[dict(type='input_text',text=payload.get('message',''))]
        if payload.get('images'): content.append(dict(type='input_image'))
        user,attachment_only=user_message(content)
    if tag=='response_item' and kind=='message' and payload.get('role')=='user':
        user,attachment_only=user_message(payload.get('content'))
    if user and not is_context(user):
        if user!=info.get('latest_request'):
            info['commentary']=''; info['commentary_at']=''; info['activity_history']=[]
        info['latest_request']=user; info['request_at']=text(stamp,64)
        label=('이미지 첨부 확인 · 실행 기록 확인 중' if user.startswith('이미지 첨부') else '파일 첨부 확인 · 실행 기록 확인 중') if attachment_only else '최신 지시 수신 · 실행 기록 확인 중'
        update_activity(info,label,stamp)
        return
    if tag!='response_item': return
    if kind=='message' and payload.get('role')=='assistant' and (payload.get('phase') or payload.get('channel'))=='commentary':
        message=message_text(payload.get('content'),'output_text')
        if message:
            info['commentary']=message; info['commentary_at']=text(stamp,64)
            update_activity(info,'AI가 진행 상황을 알렸습니다',stamp)
    elif kind in ('function_call','custom_tool_call'):
        name=payload.get('name')
        if not isinstance(name,str): return
        label=tool_label(name,payload); call=text(payload.get('call_id'),128)
        calls=info.setdefault('activity_calls',{})
        if call:
            if len(calls)>=64: calls.pop(next(iter(calls)))
            calls[call]=label
        update_activity(info,label,stamp)
    elif kind in ('function_call_output','custom_tool_call_output'):
        call=text(payload.get('call_id'),128); calls=info.setdefault('activity_calls',{})
        if call not in calls: return
        calls.pop(call)
        update_activity(info,next(reversed(calls.values())) if calls else '도구 응답 수신 · 다음 활동 확인 중',stamp)


def stamp_age(stamp,now=None):
    try:
        moment=datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
        age=max(0,int((time.time() if now is None else now)-moment))
        return age
    except (TypeError,ValueError,AttributeError,OverflowError): return None


def local_stamp(value):
    try: return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone().strftime('%m/%d %H:%M:%S')
    except (TypeError,ValueError,AttributeError,OverflowError): return ''


def activity_fields(info,category):
    activity=text(info.get('activity'),160)
    age=stamp_age(info.get('activity_at'))
    if category=='complete': activity='실행 종료 확인'
    elif category=='waiting': activity='사용자 답변·승인 대기'
    elif category=='unknown': activity='실행 상태를 다시 확인해야 합니다'
    elif category=='idle': activity='새 작업 시작 대기'
    elif not activity: activity='현재 활동 기록 미제공'
    elif age is not None and age>120: activity='새 활동 기록 대기 · 최근: '+activity
    updated=('갱신 시각 미확인' if age is None else '방금 관측' if age<10 else f'{age}초 전 관측' if age<60 else f'{age//60}분 전 관측')
    return dict(latest_request=text(info.get('latest_request')) or '최신 지시 미제공',
                current_activity=activity,activity_updated=updated,commentary=text(info.get('commentary')),
                commentary_at=local_stamp(info.get('commentary_at')),activity_history=list(info.get('activity_history',[]))[-6:])

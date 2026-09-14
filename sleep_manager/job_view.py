"""Read-only display metadata. Never feeds titles or plans into power decisions."""
import json
from pathlib import Path
import sqlite3
import time
import unicodedata
from .activity_view import observe_activity, activity_fields


def clean_text(value, limit=160):
    if not isinstance(value, str):
        return ''
    value=value[:limit*8]
    value=' '.join(''.join(c if not unicodedata.category(c).startswith('C') else ' ' for c in value).split())
    return value[:limit] + ('…' if len(value)>limit else '')


def project_name(value):
    if not isinstance(value,str): return ''
    return clean_text(value.replace('\\','/').rstrip('/').rsplit('/',1)[-1],80)


def parent_id(source):
    """Read only explicit parent identity; names are not relationship evidence."""
    try:
        if isinstance(source,str):
            if len(source)>65536: return ''
            source=json.loads(source)
        value=source['subagent']['thread_spawn'].get('parent_thread_id')
        if isinstance(value,str) and 0<len(value)<=128 and clean_text(value,128)==value:
            return value
    except (ValueError,TypeError,KeyError,AttributeError,RecursionError):
        pass
    return ''


def observe_details(info, record):
    """Read explicitly supported display records; never feed contents into the engine."""
    if not isinstance(record,dict): return
    payload=record.get('payload')
    if not isinstance(payload,dict): return
    tag=record.get('type')
    observe_activity(info,record)
    if tag=='session_meta':
        info['project']=project_name(payload.get('cwd'))
        parent=parent_id(payload.get('source'))
        if parent: info['parent_id']=parent
    elif tag=='event_msg' and payload.get('type')=='task_started':
        info['plan']=[]
    elif tag=='response_item' and payload.get('type')=='function_call' and payload.get('name') in ('update_plan','functions.update_plan'):
        info['plan']=[]
        try:
            data=payload.get('arguments',{})
            if isinstance(data,str):
                if len(data)>65536: return
                data=json.loads(data)
            plan=data.get('plan') if isinstance(data,dict) else None
            if not isinstance(plan,list) or len(plan)>40: return
            steps=[]
            for item in plan:
                if not isinstance(item,dict) or item.get('status') not in ('pending','in_progress','completed'): return
                step=clean_text(item.get('step'))
                if not step: return
                steps.append(dict(step=step,status=item['status']))
            info['plan']=steps
        except (ValueError,TypeError,RecursionError):
            pass


class JobCatalog:
    def __init__(self,codex_home,clock=time.monotonic):
        self.root=Path(codex_home).resolve()
        self.clock=clock
        self.cache={}
        self.next_read=0
        self.keys=set()

    def lookup(self,jobs):
        ids={j.session_id for j in jobs if j.provider=='codex'}
        if ids==self.keys and self.clock()<self.next_read: return self.cache
        self.keys=ids; self.next_read=self.clock()+10; result={}
        if not ids:
            self.cache={}; return self.cache
        # Explicit title index fallback. Bounded read; never read first_user_message.
        try:
            p=self.root/'session_index.jsonl'
            with p.open('rb') as f:
                offset=max(0,p.stat().st_size-1024*1024)
                f.seek(offset)
                if offset: f.readline()
                lines=f.read(1024*1024).splitlines()
            for raw in lines:
                try:
                    data=json.loads(raw)
                    if not isinstance(data,dict): continue
                    sid=data.get('id')
                    if isinstance(sid,str) and sid in ids:
                        result['codex:'+sid]={'title':clean_text(data.get('thread_name'))}
                except (ValueError,TypeError,RecursionError): pass
        except OSError: pass
        try:
            databases=sorted(self.root.glob('state_*.sqlite'),key=lambda p:p.stat().st_mtime,reverse=True)[:3]
        except OSError:
            databases=[]
        for p in databases:
            try:
                db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.15)
                try:
                    columns={row[1] for row in db.execute('PRAGMA table_info(threads)')}
                    extra=','.join(name if name in columns else 'NULL' for name in ('name','agent_nickname','agent_role','source'))
                    wanted=list(ids)
                    for i in range(0,len(wanted),200):
                        batch=wanted[i:i+200]
                        query='SELECT id,title,cwd,'+extra+' FROM threads WHERE id IN ('+','.join('?' for _ in batch)+')'
                        for sid,title,cwd,name,nickname,role,source in db.execute(query,batch):
                            info=result.setdefault('codex:'+sid,{})
                            title=clean_text(title) or clean_text(name)
                            if not title and clean_text(nickname):
                                role_name={'worker':'구현 작업','explorer':'분석 작업','shared-reviewer':'검토 작업','reviewer':'검토 작업'}.get(role,'보조 작업')
                                title=role_name+' · '+clean_text(nickname,60)
                            if title: info['title']=title
                            project=project_name(cwd)
                            if project: info['project']=project
                            parent=parent_id(source)
                            if parent: info['parent_id']=parent
                finally: db.close()
                break
            except (sqlite3.Error,OSError): continue
        self.cache=result
        return result


def job_row(job,info,action):
    tool='Codex' if job.provider=='codex' else 'Claude Code'
    category=('running' if job.active else 'unknown' if job.state=='unknown' or job.unknown_children else job.state)
    state={'running':'작업 중','waiting':'응답 필요','unknown':'확인 필요','complete':'완료','idle':'연결됨'}[category]
    if job.active and job.state!='running': state='하위 작업 중'
    title=clean_text(info.get('label')) or clean_text(info.get('title'))
    project=clean_text(info.get('project')) or clean_text(getattr(job,'project','')) or '프로젝트 미확인'
    if not title: title=f'{tool} 작업 · {job.session_id[:8]}'
    plan=info.get('plan',[]) if category!='complete' else []
    done=sum(p['status']=='completed' for p in plan)
    remaining_steps=[p for p in plan if p['status']!='completed']
    if category=='running':
        reason='아직 실행 중인 작업입니다. 이 작업이 끝나야 자동 전원 전환을 준비할 수 있습니다.'
        current=next((p for p in remaining_steps if p['status']=='in_progress'),None)
        remaining=('진행: '+current['step'] if current else f'보고된 계획 {len(remaining_steps)}개 남음') if plan else '진행 중 · 세부 단계 정보 없음'
        if plan and not remaining_steps: remaining='보고된 계획 완료 · 실행 종료 대기'
        if job.children: reason+=f' 실행 중인 하위 작업 {len(job.children)}개가 있습니다.'
    elif category=='waiting':
        reason=('사용자 답변·승인이 남아 있어 정상 종료하지 않습니다.' if action=='shutdown' else '사용자 답변·승인을 기다립니다. 다른 실행 작업이 없으면 절전 대기를 시작합니다.')
        remaining='사용자 답변 또는 승인 필요'
    elif category=='unknown':
        reason='작업 종료를 확인할 정보가 부족합니다. 연결과 원래 AI 창을 확인해 주세요.'
        remaining='작업 상태 재확인 필요'
    elif category=='complete':
        reason='작업 실행 종료가 관측되었습니다. 결과의 성공·품질을 판정한 것은 아닙니다.'
        remaining='실행 종료 확인'
    else:
        reason='세션이 연결되었습니다. 실제 작업 시작을 기다립니다.'
        remaining='작업 시작 대기'
    return dict(key=job.provider+':'+job.session_id,tool=tool,title=title,project=project,state=state,
                category=category,pending=category in ('running','waiting','unknown'),remaining=remaining,
                reason=reason,plan=plan,plan_done=done,plan_total=len(plan),session_id=job.session_id,
                parent_id=info.get('parent_id',''),**activity_fields(info,category))


def task_summary(rows):
    result={state:sum(r['category']==state for r in rows) for state in ('running','waiting','unknown','complete')}
    result['pending']=sum(r['pending'] for r in rows)
    return result


def group_jobs(rows):
    """Build a display-only forest; never mutate jobs or power decisions."""
    nodes={r['key']:dict(r,own_category=r['category'],parent_key='',is_child=bool(r.get('parent_id')),
                         relation='',child_count=0) for r in rows}
    priority={'running':0,'unknown':1,'waiting':2,'complete':3,'idle':4}
    for key,row in nodes.items():
        parent=key.split(':',1)[0]+':'+row.get('parent_id','')
        if row['is_child']:
            if parent in nodes and parent!=key: row['parent_key']=parent
            else: row['relation']='하위 작업 · 부모 작업 정보 없음'
    # Invalid relationships must not hide sessions or create cyclic widget trees.
    for key in nodes:
        path=[]; seen=set(); cursor=key
        while cursor and cursor not in seen:
            seen.add(cursor); path.append(cursor); cursor=nodes[cursor]['parent_key']
        if cursor:
            for member in path[path.index(cursor):]:
                nodes[member]['parent_key']=''
                nodes[member]['relation']='하위 작업 · 부모 관계 확인 필요'
    children={key:[] for key in nodes}
    for key,row in nodes.items():
        if row['parent_key']:
            children[row['parent_key']].append(key)
            row['relation']='상위 작업: '+nodes[row['parent_key']]['title']
    roots=[key for key,row in nodes.items() if not row['parent_key']]
    order=[]; stack=list(roots)
    while stack:
        key=stack.pop(); order.append(key); stack.extend(children[key])
    for key in reversed(order):
        row=nodes[key]
        descendants=[nodes[c] for c in children[key]]
        row['child_count']=sum(1+c['child_count'] for c in descendants)
        pending=[c for c in descendants if c['pending']]
        if not pending: continue
        category=min([row['category']]+[c['category'] for c in pending],key=priority.get)
        row['category']=category; row['pending']=True
        if category!=row['own_category']:
            row['state']={'running':'하위 작업 중','unknown':'하위 확인 필요','waiting':'하위 응답 필요'}[category]
            row['remaining']={'running':'하위 작업 실행 종료 대기','unknown':'하위 작업 상태 확인 필요','waiting':'하위 작업에 답변·승인 필요'}[category]
            row['current_activity']=row['remaining']
            row['reason']='이 작업에서 시작한 하위 작업이 남아 있습니다. 아래 하위 작업의 상태를 확인해 주세요.'
    def sort_key(key):
        r=nodes[key]
        return priority[r['category']],r['project'],r['title'],key
    result=[]; stack=sorted(roots,key=sort_key,reverse=True)
    while stack:
        key=stack.pop(); result.append(nodes[key])
        stack.extend(sorted(children[key],key=sort_key,reverse=True))
    return result


def group_summary(rows):
    counts=task_summary([r for r in rows if not r['is_child']])
    for state in ('running','waiting','unknown','complete'):
        counts['child_'+state]=sum(r['is_child'] and r['own_category']==state for r in rows)
    counts['child_pending']=sum(r['is_child'] and r['own_category'] in ('running','waiting','unknown') for r in rows)
    return counts

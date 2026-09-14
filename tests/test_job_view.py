import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sleep_manager.engine import Job
from sleep_manager.storage import Store
from sleep_manager.job_view import JobCatalog, job_row, task_summary, observe_details


class JobViewTests(unittest.TestCase):
    def test_title_project_and_remaining_plan_are_readable(self):
        job=Job('codex','uuid',state='running')
        info=dict(title='로그인 오류 수정',project='web-app',plan=[{'step':'원인 조사','status':'completed'},{'step':'회귀 테스트','status':'in_progress'}])
        row=job_row(job,info,'sleep')
        self.assertEqual(row['title'],'로그인 오류 수정')
        self.assertEqual(row['project'],'web-app')
        self.assertIn('회귀 테스트',row['remaining'])
        self.assertEqual(row['plan_done'],1)
        self.assertEqual(row['plan_total'],2)

    def test_missing_metadata_never_invents_progress(self):
        row=job_row(Job('claude','abc12345',state='running'),{},'shutdown')
        self.assertIn('Claude Code',row['title'])
        self.assertEqual(row['plan_total'],0)
        self.assertIn('세부 단계',row['remaining'])

    def test_waiting_and_unknown_explain_why_work_remains(self):
        for state in ('waiting','unknown'):
            row=job_row(Job('codex','s',state=state),{},'shutdown')
            self.assertTrue(row['pending'])
            self.assertTrue(row['reason'])
        self.assertFalse(job_row(Job('codex','s',state='complete'),{},'sleep')['pending'])

    def test_child_work_not_hidden_by_parent_complete(self):
        j=Job('codex','s',state='complete',children={'child'})
        row=job_row(j,{},'sleep')
        self.assertTrue(row['pending'])
        self.assertEqual(row['category'],'running')
        self.assertIn('하위',row['state'])
        j.children.clear(); j.unknown_children={'child':0}
        row=job_row(j,{},'sleep')
        self.assertEqual(row['category'],'unknown')
        self.assertTrue(row['pending'])

    def test_summary_has_parallel_state_counts(self):
        rows=[job_row(Job('codex',x,state=x),{},'sleep') for x in ('running','waiting','unknown','complete')]
        self.assertEqual(task_summary(rows),dict(running=1,waiting=1,unknown=1,complete=1,pending=3))

    def test_plan_only_accepts_explicit_plan_and_resets_on_new_turn(self):
        info={}
        observe_details(info,{'type':'session_meta','payload':{'cwd':'C:/work/web-app'}})
        self.assertEqual(info['project'],'web-app')
        observe_details(info,{'type':'response_item','payload':{'type':'function_call','name':'update_plan','arguments':json.dumps({'plan':[{'step':'테스트','status':'pending'}]})}})
        self.assertEqual(info['plan'][0]['step'],'테스트')
        observe_details(info,{'type':'event_msg','payload':{'type':'task_started','turn_id':'next'}})
        self.assertEqual(info['plan'],[])
        observe_details(info,{'type':'response_item','payload':{'type':'function_call','name':'exec_command','arguments':'PRIVATE'}})
        self.assertNotIn('PRIVATE',repr(info))
        observe_details(info,{'type':'response_item','payload':{'type':'function_call','name':'update_plan','arguments':'invalid'}})
        self.assertEqual(info['plan'],[])

    def test_alias_is_local_and_separate_from_event_payload(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)); store.set_label('codex','s','나의 테스트 작업')
            self.assertEqual(store.labels()['codex:s'],'나의 테스트 작업')
            self.assertEqual(store.recent(),[])
            store.set_label('codex','s','')
            self.assertNotIn('codex:s',store.labels())

    def test_catalog_reads_titles_without_private_message_fields(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); db=sqlite3.connect(root/'state_5.sqlite')
            db.execute('create table threads(id text,title text,cwd text,first_user_message text)')
            db.execute('insert into threads values(?,?,?,?)',('s','자동 종료 옵션','C:/work/sleep-manager','PRIVATE'))
            db.commit(); db.close()
            catalog=JobCatalog(root)
            result=catalog.lookup([Job('codex','s')])
            self.assertEqual(result['codex:s']['title'],'자동 종료 옵션')
            self.assertEqual(result['codex:s']['project'],'sleep-manager')
            self.assertNotIn('PRIVATE',repr(result))

    def test_index_fallback_and_invalid_database_do_not_break_display(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'state_5.sqlite').write_text('not sqlite')
            (root/'session_index.jsonl').write_text(json.dumps(dict(id='s',thread_name='색인 제목'))+'\n',encoding='utf-8')
            result=JobCatalog(root).lookup([Job('codex','s')])
            self.assertEqual(result['codex:s']['title'],'색인 제목')

class DisplayEdgeTests(unittest.TestCase):
    def test_reported_plan_complete_is_not_job_completion(self):
        row=job_row(Job('codex','s',state='running'),{'plan':[{'step':'검증','status':'completed'}]},'sleep')
        self.assertTrue(row['pending'])
        self.assertIn('실행 종료 대기',row['remaining'])

    def test_agent_name_is_used_when_title_is_missing(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); db=sqlite3.connect(root/'state_5.sqlite')
            db.execute('create table threads(id text,title text,cwd text,agent_nickname text,agent_role text)')
            db.execute('insert into threads values(?,?,?,?,?)',('s','','C:/work/app','Turing','worker'))
            db.commit(); db.close()
            self.assertEqual(JobCatalog(root).lookup([Job('codex','s')])['codex:s']['title'],'구현 작업 · Turing')

    def test_malformed_plan_clears_old_steps_without_throwing(self):
        info={'plan':[{'step':'old','status':'pending'}]}
        observe_details(info,{'type':'response_item','payload':{'type':'function_call','name':'functions.update_plan','arguments':json.dumps({'plan':[{'step':42,'status':'pending'}]})}})
        self.assertEqual(info['plan'],[])

@unittest.skipUnless(__import__('os').name=='nt','Windows Tk UI')
class ReadableUITests(unittest.TestCase):
    def test_filter_alias_selection_and_plan_details(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from sleep_manager.controller import Controller
        from sleep_manager.ui import Window
        from test_app import Power,Reader
        with tempfile.TemporaryDirectory() as d:
            c=Controller(Store(Path(d)),Power(),Reader())
            c._accept(dict(provider='codex',session_id='s',kind='start'))
            c._accept(dict(provider='claude',session_id='done',kind='complete'))
            w=Window(c,SimpleNamespace(smoke_ui=False,data_dir=Path(d)),[])
            w.root.withdraw()
            w.catalog=SimpleNamespace(lookup=lambda jobs:{'codex:s':dict(title='로그인 오류 수정',project='app',plan=[dict(step='회귀 테스트',status='in_progress')])})
            try:
                w.render_jobs()
                self.assertEqual(len(w.table.get_children()),1)
                self.assertIn('로그인 오류 수정',w.details.get('1.0','end'))
                self.assertIn('[진행] 회귀 테스트',w.details.get('1.0','end'))
                with patch('sleep_manager.ui.simpledialog.askstring',return_value='내 로그인 수정'): w.rename_job()
                self.assertIn('내 로그인 수정',w.table.item('codex:s')['text'])
                w.render_jobs(); self.assertEqual(w.table.selection(),('codex:s',))
                w.show_complete.set(True); w.render_jobs()
                self.assertEqual(len(w.table.get_children()),2)
                self.assertTrue(c.engine.jobs['codex:s'].active)
            finally: w.close()

class DisplayIsolationTests(unittest.TestCase):
    def make_log(self,root):
        return root/'rollout-00000000-0000-0000-0000-000000000001.jsonl'

    def test_deep_display_arguments_do_not_lose_following_start(self):
        from sleep_manager.codex_logs import CodexLogs
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); p=self.make_log(root)
            records=[{'type':'response_item','payload':{'type':'function_call','name':'update_plan','arguments':'['*1200+'0'+']'*1200}},
                     {'type':'event_msg','payload':{'type':'task_started','turn_id':'new'}}]
            p.write_text(''.join(json.dumps(x)+'\n' for x in records),encoding='utf-8')
            events=CodexLogs(root).poll()
            self.assertEqual(events[-1]['kind'],'start')

    def test_unexpected_display_failure_does_not_change_lifecycle(self):
        from sleep_manager.codex_logs import CodexLogs
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); p=self.make_log(root)
            p.write_text(json.dumps({'type':'event_msg','payload':{'type':'task_started'}})+'\n',encoding='utf-8')
            with patch('sleep_manager.codex_logs.observe_details',side_effect=RuntimeError('display only')):
                self.assertEqual(CodexLogs(root).poll()[-1]['kind'],'start')

    def test_skipped_or_truncated_log_clears_previous_plan(self):
        from sleep_manager.codex_logs import CodexLogs
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); p=self.make_log(root)
            plan={'type':'response_item','payload':{'type':'function_call','name':'update_plan','arguments':json.dumps({'plan':[{'step':'OLD TURN','status':'pending'}]})}}
            p.write_text(json.dumps(plan)+'\n',encoding='utf-8')
            reader=CodexLogs(root); reader.poll()
            sid='00000000-0000-0000-0000-000000000001'
            self.assertTrue(reader.details[sid]['plan'])
            records=[{'type':'event_msg','payload':{'type':'task_started','turn_id':'new'}},
                     {'type':'response_item','payload':{'type':'message','content':'padding'*1000}},
                     {'type':'response_item','payload':{'type':'function_call','name':'request_user_input','call_id':'c'}}]
            with p.open('a',encoding='utf-8') as f:
                for record in records: f.write(json.dumps(record)+'\n')
            with patch('sleep_manager.codex_logs.MAX_READ',400): reader.poll()
            self.assertEqual(reader.details[sid].get('plan',[]),[])
            reader.details[sid]['plan']=[dict(step='stale',status='pending')]
            p.write_text('{}\n',encoding='utf-8'); reader.poll()
            self.assertEqual(reader.details[sid].get('plan',[]),[])

class DisplayLargeEOFTests(unittest.TestCase):
    def test_large_last_line_clears_plan_before_early_continue(self):
        from sleep_manager.codex_logs import CodexLogs
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); p=root/'rollout-00000000-0000-0000-0000-000000000001.jsonl'
            p.write_text('{}\n',encoding='utf-8'); reader=CodexLogs(root); reader.poll()
            sid='00000000-0000-0000-0000-000000000001'
            reader.details[sid]['plan']=[dict(step='old',status='pending')]
            with p.open('a',encoding='utf-8') as f:
                f.write(json.dumps(dict(type='event_msg',payload=dict(type='task_started')))+'\n')
                f.write(json.dumps(dict(type='response_item',payload=dict(type='function_call_output',output='X'*2000)))+'\n')
            with patch('sleep_manager.codex_logs.MAX_READ',400): reader.poll()
            self.assertEqual(reader.details[sid].get('plan',[]),[])

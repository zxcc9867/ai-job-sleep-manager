import json
import tempfile
import unittest
from pathlib import Path
from sleep_manager.engine import Job
from sleep_manager.claude_details import ClaudeDetails

class ClaudeDetailsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'project'/'session.jsonl'
        self.path.parent.mkdir();self.clock=[0]
        self.reader=ClaudeDetails(self.root,clock=lambda:self.clock[0])
        self.job=Job('claude','session',state='running')
    def row(self,role,content,**kw):
        return dict(type=role,sessionId='session',timestamp='2026-09-21T00:00:00Z',message=dict(role=role,content=content),**kw)
    def write(self,rows):
        self.path.write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8');self.clock[0]+=3
    def lookup(self):return self.reader.lookup([self.job]).get('claude:session',{})
    def test_latest_request_public_response_and_tool_without_private_content(self):
        self.write([self.row('user','고쳐줘'), self.row('assistant',[dict(type='thinking',thinking='SECRET'),dict(type='text',text='오류를 확인합니다.'),dict(type='tool_use',id='a',name='Bash',input={'command':'SECRET'})])])
        info=self.lookup()
        self.assertEqual(info['latest_request'],'고쳐줘');self.assertEqual(info['commentary'],'오류를 확인합니다.')
        self.assertIn('터미널',info['activity']);self.assertNotIn('SECRET',json.dumps(info))
        self.assertEqual(self.job.state,'running')
    def test_tool_results_and_meta_are_not_instructions(self):
        self.write([self.row('user','실제 지시'),self.row('user','자동 주입',isMeta=True),self.row('user',[dict(type='tool_result',tool_use_id='a',content='SECRET')]),self.row('assistant',[dict(type='text',text='검증 중')])])
        self.assertEqual(self.lookup()['latest_request'],'실제 지시')
    def test_new_request_clears_previous_response_even_when_text_repeats(self):
        self.write([self.row('user','진행해'),self.row('assistant',[dict(type='text',text='이전 설명')]),self.row('user','진행해')])
        self.assertFalse(self.lookup().get('commentary'));self.assertEqual(self.lookup()['latest_request'],'진행해')
    def test_unknown_sessions_and_other_session_records_never_leak(self):
        self.write([self.row('user','OTHER',session_other=True)])
        row=self.row('user','OTHER');row['sessionId']='other';self.write([row])
        self.assertFalse(self.lookup());self.assertEqual(self.reader.lookup([Job('claude','unobserved')]),{})
    def test_no_user_boundary_means_no_old_response(self):
        self.write([self.row('assistant',[dict(type='text',text='unanchored')])]);self.assertFalse(self.lookup())
    def test_file_disappears_or_corrupts_clears_cached_details(self):
        self.write([self.row('user','hello')]);self.assertTrue(self.lookup())
        self.path.unlink();self.clock[0]+=3;self.assertFalse(self.lookup())
        self.write([self.row('user','hello')]);self.assertTrue(self.lookup())
        self.path.write_text(self.path.read_text()+'BROKEN\n',encoding='utf-8');self.clock[0]+=3;self.assertFalse(self.lookup())
    def test_pending_new_hook_request_does_not_show_previous_turn(self):
        self.write([self.row('user','old')]);self.assertTrue(self.lookup())
        self.assertEqual(self.reader.lookup([self.job],{'session':2000000000}),{})
    def test_bounded_tail_without_request_does_not_inherit_old_turn(self):
        from unittest.mock import patch
        self.write([self.row('user','old'),self.row('assistant',[dict(type='text',text='x'*3000)])])
        with patch('sleep_manager.claude_details.MAX_READ',1024):self.assertFalse(self.lookup())
    def test_image_only_and_tool_result_labels(self):
        self.write([self.row('user',[dict(type='image',source={'data':'SECRET'})]), self.row('assistant',[dict(type='tool_use',id='a',name='Read')]),self.row('user',[dict(type='tool_result',tool_use_id='a',content='SECRET')])])
        info=self.lookup();self.assertIn('이미지',info['latest_request']);self.assertIn('도구 응답',info['activity']);self.assertNotIn('SECRET',str(info))

@unittest.skipUnless(__import__('os').name=='nt','Windows Tk UI')
class ClaudeDetailsUITests(unittest.TestCase):
    def test_transcript_reaches_selected_job_details_without_changing_state(self):
        from types import SimpleNamespace
        from sleep_manager.controller import Controller
        from sleep_manager.storage import Store
        from sleep_manager.ui import Window
        from test_app import Reader, Power
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'projects'/'demo'/'session.jsonl';path.parent.mkdir(parents=True)
            rows=[dict(type='user',sessionId='session',message=dict(role='user',content='API 테스트해줘')),
                  dict(type='assistant',sessionId='session',message=dict(role='assistant',content=[dict(type='text',text='API 응답을 검증하고 있습니다.')]))]
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
            c=Controller(Store(root/'data'),Power(),Reader())
            c._accept(dict(provider='claude',session_id='session',kind='start'))
            w=Window(c,SimpleNamespace(smoke_ui=False,data_dir=root/'data'),[]);w.root.withdraw()
            try:
                w.claude_details=ClaudeDetails(root/'projects');w.render_jobs()
                w.table.selection_set('claude:session');w.show_job_details()
                detail=w.details.get('1.0','end')
                self.assertIn('API 테스트해줘',detail);self.assertIn('API 응답을 검증',detail)
                self.assertEqual(c.engine.jobs['claude:session'].state,'running')
            finally:w.close()

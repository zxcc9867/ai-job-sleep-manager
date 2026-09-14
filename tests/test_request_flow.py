import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from sleep_manager.codex_logs import CodexLogs
from sleep_manager.engine import Job
from sleep_manager.job_view import job_row,group_jobs
from sleep_manager.activity_view import user_message

SID='00000000-0000-0000-0000-000000000001'
def record(tag,kind,**kwargs):
    return dict(type=tag,timestamp='2026-09-14T12:00:00Z',payload=dict(type=kind,**kwargs))
def directive(value):
    return record('response_item','message',role='user',content=[dict(type='input_text',text=value)])
def turn(value): return record('event_msg','task_started',turn_id=value)
def filler(): return record('response_item','function_call_output',call_id='x',output='x'*1800)

class RequestFlowTests(unittest.TestCase):
    def read(self,records):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/('rollout-'+SID+'.jsonl')
            p.write_text(''.join(json.dumps(r)+'\n' for r in records),encoding='utf-8')
            reader=CodexLogs(d)
            with patch('sleep_manager.codex_logs.MAX_READ',2048): events=reader.poll()
            return reader.details.get(SID,{}),events

    def test_request_outside_tail_is_recovered_without_changing_lifecycle(self):
        info,events=self.read([turn('current'),directive('구현해줘')]+[filler()]*5)
        self.assertEqual(info.get('latest_request'),'구현해줘')
        self.assertEqual(info.get('request_turn'),'current')
        self.assertEqual([e['kind'] for e in events],['unknown'])

    def test_large_tool_output_between_request_and_tail_does_not_hide_request(self):
        from sleep_manager.request_history import recover_request
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'log.jsonl'
            large=record('response_item','function_call_output',call_id='x',output='x'*(5*1024*1024))
            p.write_text(''.join(json.dumps(r)+'\n' for r in [turn('current'),directive('구현해줘'),large,filler()]),encoding='utf-8')
            self.assertEqual(recover_request(p,p.stat().st_size).get('latest_request'),'구현해줘')

    def test_new_turn_without_user_never_inherits_previous_request(self):
        info,_=self.read([turn('old'),directive('OLD'),turn('new')]+[filler()]*5)
        self.assertFalse(info.get('latest_request'))

    def test_latest_steering_wins_and_context_is_not_a_request(self):
        info,_=self.read([turn('current'),directive('OLD'),directive('테스트만 해줘'),directive('<environment_context>context</environment_context>')]+[filler()]*5)
        self.assertEqual(info.get('latest_request'),'테스트만 해줘')

    def test_recovery_requires_turn_boundary_and_stays_bounded(self):
        from sleep_manager.request_history import recover_request
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'log.jsonl'
            p.write_text(''.join(json.dumps(r)+'\n' for r in [turn('current'),directive('OLD')]+[filler()]*5),encoding='utf-8')
            self.assertEqual(recover_request(p,p.stat().st_size,max_bytes=2048),{})
            self.assertEqual(recover_request(p,p.stat().st_size,expected_turn='different'),{})

    def test_mixed_context_blocks_do_not_swallow_user_request(self):
        request,_=user_message([dict(type='input_text',text='<recommended_plugins>context</recommended_plugins>'),dict(type='input_text',text='오류를 고쳐줘')])
        self.assertEqual(request,'오류를 고쳐줘')

    def test_parent_request_is_context_not_fabricated_child_instruction(self):
        rows=group_jobs([job_row(Job('codex','p',state='complete'),dict(latest_request='구현해줘'),'sleep'),job_row(Job('codex','c',state='running'),dict(parent_id='p',commentary='본문 누락 회귀 테스트를 추가하고 있습니다.'),'sleep')])
        parent,child=rows
        self.assertEqual(child['parent_request'],'구현해줘')
        self.assertEqual(child['latest_request'],'최신 지시 미제공')
        self.assertIn('상위 요청',child['request_display'])
        self.assertIn('본문 누락',parent['child_work'][0]['description'])

    def test_own_child_instruction_and_missing_parent_remain_distinct(self):
        rows=group_jobs([job_row(Job('codex','c',state='running'),dict(parent_id='missing',latest_request='검토해줘'),'sleep')])
        self.assertEqual(rows[0]['request_display'],'검토해줘')
        self.assertFalse(rows[0]['parent_request'])

    def test_no_parent_request_does_not_use_old_conversation_title(self):
        rows=group_jobs([job_row(Job('codex','p',state='running'),dict(title='OLD'),'sleep'),job_row(Job('codex','c',state='running'),dict(parent_id='p'),'sleep')])
        self.assertNotIn('OLD',rows[1]['request_display'])
        self.assertFalse(rows[1]['parent_request'])


@unittest.skipUnless(__import__('os').name=='nt','Windows Tk UI')
class RequestFlowUITests(unittest.TestCase):
    def test_parent_shows_remaining_child_work_and_child_shows_request_provenance(self):
        from types import SimpleNamespace
        from sleep_manager.controller import Controller
        from sleep_manager.storage import Store
        from sleep_manager.ui import Window
        from test_app import Power,Reader
        with tempfile.TemporaryDirectory() as d:
            reader=Reader(); reader.details={'p':dict(latest_request='구현해줘'),'c':dict(parent_id='p',commentary='긴 본문 누락 회귀 테스트를 추가하고 있습니다.')}
            c=Controller(Store(Path(d)),Power(),reader)
            for sid in ['p','c']: c._accept(dict(provider='codex',session_id=sid,kind='start'))
            w=Window(c,SimpleNamespace(smoke_ui=False,data_dir=Path(d)),[]);w.root.withdraw()
            w.catalog=SimpleNamespace(lookup=lambda jobs:{'codex:p':dict(title='기술 피드 개선'),'codex:c':dict(title='구현 작업 · Fermat')})
            try:
                w.render_jobs();w.table.selection_set('codex:p');w.show_job_details()
                detail=w.details.get('1.0','end')
                self.assertIn('남은 하위 작업',detail)
                self.assertIn('긴 본문 누락 회귀 테스트',detail)
                w.table.selection_set('codex:c');w.show_job_details()
                detail=w.details.get('1.0','end')
                self.assertIn('상위 사용자 지시: 구현해줘',detail)
                self.assertIn('하위 지시 본문은 기록에 제공되지 않았습니다',detail)
                self.assertIn('상위 요청: 구현해줘',w.table.item('codex:c','values'))
            finally:w.close()

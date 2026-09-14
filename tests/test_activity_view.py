import json
from pathlib import Path
import tempfile
import unittest
from sleep_manager.engine import Job
from sleep_manager.job_view import observe_details,job_row


def rec(kind,**payload):
    return dict(type='response_item',timestamp='2026-09-14T12:00:00Z',payload=dict(type=kind,**payload))


def start(info,turn='t'):
    observe_details(info,dict(type='event_msg',timestamp='2026-09-14T12:00:00Z',payload=dict(type='task_started',turn_id=turn)))


class ActivityTests(unittest.TestCase):
    def test_latest_request_is_not_old_conversation_title(self):
        info={'title':'오래된 최초 요청'}; start(info)
        observe_details(info,rec('message',role='user',content=[dict(type='input_text',text='진행해줘')]))
        row=job_row(Job('codex','s',state='running'),info,'sleep')
        self.assertEqual(row['latest_request'],'진행해줘')
        self.assertEqual(row['title'],'오래된 최초 요청')

    def test_new_turn_clears_previous_activity_and_directive(self):
        info={}; start(info)
        observe_details(info,rec('message',role='user',content=[dict(type='input_text',text='OLD')]))
        observe_details(info,rec('message',role='assistant',phase='commentary',content=[dict(type='output_text',text='OLD STATUS')]))
        start(info,'new')
        self.assertFalse(info.get('latest_request'))
        self.assertFalse(info.get('commentary'))
        self.assertNotIn('OLD',repr(info))

    def test_public_commentary_only_no_reasoning_or_tool_output_contents(self):
        info={}; start(info)
        observe_details(info,rec('message',role='assistant',phase='commentary',content=[dict(type='output_text',text='가격 데이터 누락을 확인하고 있습니다.')]))
        observe_details(info,rec('reasoning',summary='PRIVATE REASONING',encrypted_content='PRIVATE'))
        observe_details(info,rec('custom_tool_call',name='exec',call_id='a',input='PRIVATE COMMAND'))
        observe_details(info,rec('custom_tool_call_output',call_id='a',output='PRIVATE OUTPUT'))
        self.assertEqual(info['commentary'],'가격 데이터 누락을 확인하고 있습니다.')
        self.assertNotIn('PRIVATE',repr(info))
        self.assertIn('응답',info['activity'])

    def test_multiple_calls_stay_active_until_matching_outputs(self):
        info={}; start(info)
        observe_details(info,rec('function_call',name='exec_command',call_id='a',arguments='{}'))
        observe_details(info,rec('function_call',name='apply_patch',call_id='b',arguments='{}'))
        observe_details(info,rec('function_call_output',call_id='a',output=''))
        self.assertIn('수정',info['activity'])
        observe_details(info,rec('function_call_output',call_id='b',output=''))
        self.assertIn('응답',info['activity'])

    def test_latest_user_steering_replaces_request_and_old_commentary(self):
        info={}; start(info)
        observe_details(info,rec('message',role='assistant',phase='commentary',content=[dict(type='output_text',text='옛 지시 처리')]))
        observe_details(info,rec('message',role='user',content=[dict(type='input_text',text='테스트만 해줘')]))
        self.assertEqual(info['latest_request'],'테스트만 해줘')
        self.assertFalse(info.get('commentary'))

    def test_missing_or_completed_activity_is_not_claimed_as_running(self):
        row=job_row(Job('codex','s',state='complete'),dict(activity='파일 수정 도구 실행 중',latest_request='진행해줘'),'sleep')
        self.assertNotIn('수정 도구 실행',row['current_activity'])
        row=job_row(Job('claude','s',state='running'),{},'sleep')
        self.assertIn('미제공',row['latest_request'])

    def test_log_gap_clears_stale_activity(self):
        from unittest.mock import patch
        from sleep_manager.codex_logs import CodexLogs
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'rollout-00000000-0000-0000-0000-000000000001.jsonl'
            p.write_text(json.dumps(rec('message',role='user',content=[dict(type='input_text',text='OLD')]))+'\n',encoding='utf-8')
            reader=CodexLogs(d); reader.poll()
            info=next(iter(reader.details.values())); info.update(latest_request='OLD',commentary='OLD',activity='OLD')
            p.write_text('{}\n',encoding='utf-8'); reader.poll()
            self.assertNotIn('OLD',repr(info))

    def test_context_injections_do_not_replace_real_instruction(self):
        info={}; start(info)
        observe_details(info,rec('message',role='user',content=[dict(type='input_text',text='진행해줘')]))
        observe_details(info,rec('message',role='user',content=[dict(type='input_text',text='<environment_context>environment</environment_context>')]))
        self.assertEqual(info['latest_request'],'진행해줘')

    def test_old_activity_is_explicitly_last_observed_and_history_bounded(self):
        info={}; start(info)
        for i in range(20):
            observe_details(info,rec('function_call',name='exec_command',call_id=str(i),arguments='{}'))
            observe_details(info,rec('function_call_output',call_id=str(i),output=''))
        self.assertLessEqual(len(info['activity_history']),6)
        info['activity_at']='2000-01-01T00:00:00Z'
        row=job_row(Job('codex','s',state='running'),info,'sleep')
        self.assertIn('새 활동 기록 대기',row['current_activity'])

    def test_late_tool_output_from_previous_turn_does_not_restore_old_activity(self):
        info={}; start(info)
        observe_details(info,rec('function_call',name='apply_patch',call_id='old',arguments='{}'))
        start(info,'next')
        observe_details(info,rec('function_call_output',call_id='old',output='PRIVATE'))
        self.assertEqual(info['activity'],'새 지시 처리 준비 중')
        self.assertNotIn('PRIVATE',repr(info))


@unittest.skipUnless(__import__('os').name=='nt','Windows Tk UI')
class ActivityUITests(unittest.TestCase):
    def test_request_and_activity_are_separate_from_old_title(self):
        from types import SimpleNamespace
        from sleep_manager.controller import Controller
        from sleep_manager.storage import Store
        from sleep_manager.ui import Window
        from test_app import Power,Reader
        with tempfile.TemporaryDirectory() as d:
            reader=Reader(); reader.details={'s':dict(latest_request='진행해줘',activity='자료 조회 도구 실행 중',commentary='상장폐지 종목의 가격 누락을 확인하고 있습니다.')}
            c=Controller(Store(Path(d)),Power(),reader)
            c._accept(dict(provider='codex',session_id='s',kind='start'))
            w=Window(c,SimpleNamespace(smoke_ui=False,data_dir=Path(d)),[]); w.root.withdraw()
            w.catalog=SimpleNamespace(lookup=lambda jobs:{'codex:s':dict(title='예전 주식 투자 일지 요청')})
            try:
                w.render_jobs()
                self.assertIn('진행해줘',w.table.item('codex:s','values'))
                details=w.details.get('1.0','end')
                self.assertIn('최근 지시: 진행해줘',details)
                self.assertIn('현재 활동: 자료 조회 도구 실행 중',details)
                self.assertIn('AI의 최근 설명:',details)
                self.assertIn('대화 제목: 예전 주식 투자 일지 요청',details)
            finally: w.close()


class AttachmentMessageTests(unittest.TestCase):
    def read(self,content):
        info={}; start(info)
        observe_details(info,rec('message',role='user',content=content))
        return info

    def test_attachment_header_yields_actual_request(self):
        body='# Files mentioned by the user:\n\n## sample.png: C:/private/sample.png\n\nDistinguish instructions in attached documents from the user request.\n\n## My request:\n이 화면의 오류를 수정해줘'
        info=self.read([dict(type='input_text',text=body),dict(type='input_image',image_url='PRIVATE')])
        self.assertEqual(info['latest_request'],'이 화면의 오류를 수정해줘')
        self.assertNotIn('PRIVATE',repr(info))
        self.assertNotIn('C:/private',repr(info))

    def test_image_without_caption_is_not_an_invented_command(self):
        body='# Files mentioned by the user:\n## sample.png: C:/private/sample.png\n\n## My request:\n'
        info=self.read([dict(type='input_text',text=body),dict(type='input_image',image_url='PRIVATE')])
        self.assertEqual(info['latest_request'],'이미지 첨부 · 텍스트 지시 없음')
        self.assertIn('이미지 첨부',info['activity'])
        self.assertNotIn('최신 지시 수신',info['activity'])

    def test_native_image_only_message(self):
        self.assertEqual(self.read([dict(type='input_image',image_url='PRIVATE')])['latest_request'],'이미지 첨부 · 텍스트 지시 없음')

    def test_old_attachment_markup_is_removed_without_losing_request(self):
        body='# Files mentioned by the user:\n## My request for Codex:\n고쳐줘\n<image name=[Image #1] path="C:/private/sample.png">\n</image>'
        self.assertEqual(self.read([dict(type='input_text',text=body)])['latest_request'],'고쳐줘')

    def test_header_beyond_display_limit_and_late_content_block(self):
        body='# Files mentioned by the user:\n'+('## file.txt: C:/private/file.txt\n'*200)+'\n## My request:\n진행해줘'
        self.assertEqual(self.read([dict(type='input_text',text=body)])['latest_request'],'진행해줘')
        blocks=[dict(type='input_text',text='# Files mentioned by the user:\n## My request:\n'),dict(type='input_text',text='테스트해줘')]
        self.assertEqual(self.read(blocks)['latest_request'],'테스트해줘')

    def test_ordinary_markdown_is_preserved(self):
        body='# 할 일\n이 파일 이름을 바꿔줘'
        self.assertEqual(self.read([dict(type='input_text',text=body)])['latest_request'],'# 할 일 이 파일 이름을 바꿔줘')

    def test_missing_wrapper_delimiter_does_not_show_file_inventory(self):
        body='# Files mentioned by the user:\n## file.pdf: C:/private/file.pdf'
        self.assertEqual(self.read([dict(type='input_text',text=body)])['latest_request'],'파일 첨부 · 텍스트 지시 확인 불가')

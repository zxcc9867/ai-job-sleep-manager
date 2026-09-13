import json
import tempfile
import unittest
from pathlib import Path
from sleep_manager.codex_logs import CodexLogs, parse_record

class LogTests(unittest.TestCase):
    def test_lifecycle_never_stores_message(self):
        e=parse_record({'type':'event_msg','timestamp':'2026-09-13T00:00:00Z','payload':{'type':'task_complete','turn_id':'t','last_agent_message':'SECRET'}},'s')
        self.assertEqual(e['kind'],'complete')
        self.assertNotIn('SECRET',repr(e))
    def test_reasoning_or_agent_message_not_completion(self):
        for t in ['agent_message','token_count','item_completed']:
            self.assertIsNone(parse_record({'type':'event_msg','payload':{'type':t}},'s'))
    def test_tail_handles_partial_writes_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); p=root/'rollout-2026-09-13T00-00-00-00000000-0000-0000-0000-000000000001.jsonl'
            r=CodexLogs(root)
            a=json.dumps({'type':'event_msg','timestamp':'2026-09-13T00:00:00Z','payload':{'type':'task_started','turn_id':'t'}})+'\n'
            p.write_text(a,encoding='utf-8')
            self.assertEqual(r.poll()[0]['kind'],'start')
            self.assertEqual(r.poll(),[])
            b=json.dumps({'type':'event_msg','timestamp':'2026-09-13T00:01:00Z','payload':{'type':'task_complete','turn_id':'t'}})+'\n'
            with p.open('a',encoding='utf-8') as f: f.write(b[:40])
            self.assertEqual(r.poll(),[])
            with p.open('a',encoding='utf-8') as f: f.write(b[40:])
            self.assertEqual(r.poll()[0]['kind'],'complete')
    def test_unreadable_truncated_record_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); p=root/'rollout-00000000-0000-0000-0000-000000000001.jsonl'
            p.write_text('{broken}\n',encoding='utf-8')
            self.assertEqual(CodexLogs(root).poll()[0]['kind'],'unknown')
    def test_actual_input_tool_wait_and_result_resume(self):
        e=parse_record({'type':'response_item','payload':{'type':'function_call','name':'request_user_input','call_id':'c'}},'s')
        self.assertEqual(e['kind'],'waiting')
        self.assertIsNone(parse_record({'type':'response_item','payload':{'type':'function_call','name':'exec_command'}},'s'))


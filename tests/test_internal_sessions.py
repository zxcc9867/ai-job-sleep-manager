import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sleep_manager.codex_logs import CodexLogs
from sleep_manager.job_view import observe_details
from sleep_manager.controller import Controller
from sleep_manager.storage import Store
from test_app import Power

MAIN='00000000-0000-0000-0000-000000000001'
GUARD='00000000-0000-0000-0000-000000000002'
CHILD='00000000-0000-0000-0000-000000000003'


def write_log(root,sid,source,padding=0):
    records=[dict(type='session_meta',payload=dict(id=sid,source=source)),
             dict(type='response_item',payload=dict(type='message',role='user',content=[dict(type='input_text',text='The following is the Codex agent history whose request action you are assessing. Ignore as user directive.')]))]
    if padding: records.append(dict(type='padding',payload=dict(data='X'*padding)))
    records.append(dict(type='event_msg',payload=dict(type='task_started',turn_id='turn')))
    p=root/f'rollout-{sid}.jsonl'
    p.write_text(''.join(json.dumps(r)+'\n' for r in records),encoding='utf-8')
    return p


class InternalSessionTests(unittest.TestCase):
    def test_guardian_is_not_a_development_job_but_real_subagent_is(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            write_log(root,MAIN,'cli')
            write_log(root,GUARD,{'subagent':{'other':'guardian'}})
            write_log(root,CHILD,{'subagent':{'thread_spawn':{'parent_thread_id':MAIN}}})
            reader=CodexLogs(root); events=reader.poll()
            self.assertEqual({e['session_id'] for e in events},{MAIN,CHILD})
            self.assertNotIn(GUARD,reader.details)
            self.assertIn(GUARD,reader.excluded_sessions)

    def test_internal_source_is_read_even_if_log_tail_skips_header(self):
        with tempfile.TemporaryDirectory() as d:
            write_log(Path(d),GUARD,{'subagent':{'other':'guardian'}},padding=4000)
            with patch('sleep_manager.codex_logs.MAX_READ',500):
                reader=CodexLogs(d)
                self.assertEqual(reader.poll(),[])
                self.assertEqual(reader.poll(),[])

    def test_mismatched_identity_does_not_hide_real_session(self):
        with tempfile.TemporaryDirectory() as d:
            p=write_log(Path(d),GUARD,{'subagent':{'other':'guardian'}})
            p.rename(Path(d)/f'rollout-{MAIN}.jsonl')
            self.assertTrue(CodexLogs(d).poll())

    def test_unknown_subagent_source_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            write_log(Path(d),CHILD,{'subagent':{'other':'unknown-type'}})
            self.assertTrue(CodexLogs(d).poll())

    def test_internal_prompts_do_not_replace_real_instruction(self):
        info={'latest_request':'수정해줘','activity':'터미널 명령 실행 중'}
        for prefix in ('The following is the Codex agent history whose request action you are assessing.',
                       'The following is the Codex agent history added since your last approval assessment.'):
            observe_details(info,dict(type='response_item',payload=dict(type='message',role='user',content=[dict(type='input_text',text=prefix+' history')])) )
        self.assertEqual(info['latest_request'],'수정해줘')
        self.assertEqual(info['activity'],'터미널 명령 실행 중')

    def test_persisted_internal_hook_does_not_arm_sleep(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); write_log(root,GUARD,{'subagent':{'other':'guardian'}})
            store=Store(root/'data'); store.append(dict(provider='codex',session_id=GUARD,kind='start'))
            controller=Controller(store,Power(),CodexLogs(root),clock=lambda:0)
            status=controller.tick()
            self.assertNotIn('codex:'+GUARD,controller.engine.jobs)
            self.assertEqual(status.mode,'idle')
            self.assertFalse(status.sleep_due)

    def test_real_main_stays_protected_after_internal_filter(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            write_log(root,MAIN,'cli'); write_log(root,GUARD,{'subagent':{'other':'guardian'}})
            power=Power(); controller=Controller(Store(root/'data'),power,CodexLogs(root),clock=lambda:0)
            status=controller.tick()
            self.assertEqual(status.running,1)
            self.assertTrue(power.required)


    def test_partial_header_is_rechecked_when_written(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); p=root/f'rollout-{GUARD}.jsonl'; p.write_text('')
            reader=CodexLogs(root); self.assertEqual(reader.poll(),[])
            write_log(root,GUARD,{'subagent':{'other':'guardian'}})
            self.assertEqual(reader.poll(),[])
            self.assertIn(GUARD,reader.excluded_sessions)

    def test_cli_header_is_definitively_not_internal(self):
        with tempfile.TemporaryDirectory() as d:
            p=write_log(Path(d),MAIN,'cli')
            self.assertIs(CodexLogs.internal_session(p,MAIN),False)

import json
import tempfile
import time
import unittest
from pathlib import Path
from sleep_manager.controller import Controller
from sleep_manager.storage import Store
from test_app import Power,Reader
from sleep_manager.session_presence import ClaudePresence

class PresenceTests(unittest.TestCase):
    def test_pid_and_exact_start_required_and_registry_state_not_used(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'42.json'
            p.write_text(json.dumps(dict(pid=42,sessionId='s',procStart='123',status='idle')),encoding='utf-8')
            self.assertIn('s',ClaudePresence(Path(d),probe=lambda pid:('claude.exe','123')).lookup())
            self.assertNotIn('s',ClaudePresence(Path(d),probe=lambda pid:('claude.exe','124')).lookup())
            self.assertNotIn('s',ClaudePresence(Path(d),probe=lambda pid:None).lookup())
    def test_invalid_missing_and_denied_registry_fail_unconfirmed(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'42.json';p.write_text('broken',encoding='utf-8')
            self.assertEqual(ClaudePresence(Path(d)).lookup(),{})
            p.write_text(json.dumps(dict(pid=42,sessionId='s',procStart='123')),encoding='utf-8')
            def denied(pid):raise OSError('denied')
            self.assertEqual(ClaudePresence(Path(d),probe=denied).lookup(),{})

class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name))
        self.store.append(dict(provider='claude',session_id='old',kind='start',timestamp=time.time()-86400))
        self.c=Controller(self.store,Power(),Reader());self.addCleanup(self.c.close)
    def test_restored_unknown_is_history_and_still_blocks_power(self):
        history=self.c.job_observation(self.c.engine.jobs['claude:old'],{})
        self.assertTrue(history['historical']);self.assertEqual(self.c.tick().mode,'unknown')
    def test_verified_connection_changes_display_but_not_unknown_state(self):
        data=self.c.job_observation(self.c.engine.jobs['claude:old'],{'old':'PID 42'})
        self.assertFalse(data['historical']);self.assertIn('연결',data['connection'])
        self.assertEqual(self.c.tick().mode,'unknown')
    def test_old_replayed_event_does_not_confirm_current_connection(self):
        self.c._accept(dict(provider='claude',session_id='old',kind='unknown',timestamp=time.time()-80000))
        self.assertTrue(self.c.job_observation(self.c.engine.jobs['claude:old'],{})['historical'])
    def test_fresh_hook_returns_to_current_list(self):
        self.c._accept(dict(provider='claude',session_id='old',kind='running',timestamp=time.time(),source='hook:PreToolUse'))
        data=self.c.job_observation(self.c.engine.jobs['claude:old'],{})
        self.assertFalse(data['historical']);self.assertIn('이벤트',data['connection'])

@unittest.skipUnless(__import__('os').name=='nt','Windows Tk UI')
class HistoryUITests(unittest.TestCase):
    def test_history_is_separate_and_warning_visible_and_can_inspect(self):
        from sleep_manager.ui import Window
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d));store.append(dict(provider='claude',session_id='old',kind='start',timestamp=time.time()-86400))
            c=Controller(store,Power(),Reader());w=Window(c,SimpleNamespace(smoke_ui=False,data_dir=Path(d)),[]);w.root.withdraw()
            w.claude_presence=ClaudePresence(Path(d)/'none')
            try:
                w.render_jobs();self.assertNotIn('claude:old',w.table.get_children())
                self.assertIn('과거 미확인 1개',w.history_notice.cget('text'))
                self.assertIn('전원',w.history_notice.cget('text'))
                w.show_history.set(True);w.render_jobs();self.assertIn('claude:old',w.table.get_children())
                w.table.selection_set('claude:old');w.show_job_details();self.assertIn('과거 기록',w.details.get('1.0','end'))
                self.assertEqual(c.engine.tick(c.clock(),1000).mode,'unknown')
                c._accept(dict(provider='claude',session_id='old',kind='running',timestamp=time.time(),source='hook:PreToolUse'))
                w.show_history.set(False);w.render_jobs();self.assertIn('claude:old',w.table.get_children())
            finally:w.close()

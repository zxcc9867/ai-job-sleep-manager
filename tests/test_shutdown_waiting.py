"""Unanswered waits may request shutdown; active or uncertain work stays protected."""
from pathlib import Path
import tempfile
import unittest

from sleep_manager.controller import Controller
from sleep_manager.engine import Engine
from sleep_manager.storage import Store
from test_app import Reader
from test_shutdown import ShutdownPower


class WaitingShutdownTests(unittest.TestCase):
    def test_codex_and_claude_unanswered_waits_keep_state_and_allow_full_delay(self):
        for provider in ('codex','claude'):
            with self.subTest(provider=provider):
                engine=Engine(delay=60,action='shutdown')
                engine.ingest(dict(provider=provider,session_id='ask',kind='waiting'),0)
                engine.ingest(dict(provider=provider,session_id='done',kind='complete'),0)
                self.assertEqual(engine.tick(0,1000).remaining,60)
                self.assertFalse(engine.tick(59,1000).sleep_due)
                state=engine.tick(60,1000)
                self.assertTrue(state.sleep_due)
                self.assertEqual(state.waiting,1)
                self.assertEqual(engine.jobs[provider+':ask'].state,'waiting')

    def test_active_and_uncertain_work_still_veto_shutdown(self):
        for kind,extra in (('start',{}),('child_start',{'task_id':'child'}),
                           ('unknown',{}),('interrupted',{}),
                           ('child_stop',{'task_id':'lost','metadata_complete':False}),
                           ('waiting',{'background_active':True})):
            with self.subTest(kind=kind,extra=extra):
                engine=Engine(delay=60,action='shutdown')
                engine.ingest(dict(provider='codex',session_id='ask',kind='waiting'),0)
                engine.ingest(dict(provider='claude',session_id='other',kind=kind,**extra),0)
                self.assertFalse(engine.tick(5000,5000).sleep_due)
                self.assertIsNone(engine.tick(5000,5000).remaining)

    def test_response_resume_and_new_user_input_reset_full_delay(self):
        engine=Engine(delay=60,action='shutdown')
        event=dict(provider='codex',session_id='ask')
        engine.ingest(dict(event,kind='waiting'),0);engine.tick(0,1000)
        engine.ingest(dict(event,kind='running'),59)
        self.assertEqual(engine.tick(60,1000).mode,'running')
        engine.ingest(dict(event,kind='waiting'),61)
        self.assertEqual(engine.tick(61,1000).remaining,60)
        self.assertEqual(engine.tick(120,0).remaining,60)
        self.assertFalse(engine.tick(179,59).sleep_due)
        self.assertTrue(engine.tick(180,60).sleep_due)

    def test_new_wait_gets_full_delay_but_repeated_wait_does_not_postpone_forever(self):
        engine=Engine(delay=60,action='shutdown')
        engine.ingest(dict(provider='codex',session_id='done',kind='complete'),0)
        engine.tick(0,1000)
        event=dict(provider='codex',session_id='ask',kind='waiting')
        engine.ingest(event,55)
        self.assertEqual(engine.tick(55,1000).remaining,60)
        engine.ingest(event,70)
        self.assertEqual(engine.tick(70,1000).remaining,45)
        self.assertTrue(engine.tick(115,1000).sleep_due)

    def make(self,folder):
        store=Store(Path(folder));store.save_settings(dict(enabled=True,action='shutdown',delay=60))
        clock=[0];power=ShutdownPower(store)
        controller=Controller(store,power,Reader(),clock=lambda:clock[0])
        store.append(dict(provider='codex',session_id='ask',kind='waiting'))
        controller.tick()
        return store,clock,power,controller

    def test_waiting_requests_once_without_fabricating_completion_and_restart_stays_paused(self):
        with tempfile.TemporaryDirectory() as folder:
            store,clock,power,controller=self.make(folder)
            try:
                for instant in range(1,150):clock[0]=instant;controller.tick()
                self.assertEqual(power.shutdowns,1)
                self.assertFalse(store.settings()['enabled'])
                self.assertEqual(controller.engine.jobs['codex:ask'].state,'waiting')
                restored=Controller(store,ShutdownPower(store),Reader(),clock=lambda:clock[0])
                try:self.assertEqual(restored.tick().mode,'paused')
                finally:restored.close()
            finally:controller.close()

    def test_new_work_or_unknown_event_at_final_check_prevents_request(self):
        for kind in ('running','unknown'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                store,clock,power,controller=self.make(folder)
                try:
                    for instant in range(1,60):clock[0]=instant;controller.tick()
                    power.inject=lambda:store.append(dict(provider='claude',session_id='new',kind=kind))
                    clock[0]=60;controller.tick()
                    self.assertEqual(power.shutdowns,0)
                    self.assertIn('claude:new',controller.engine.jobs)
                    self.assertEqual(controller.engine.jobs['claude:new'].state,kind)
                    self.assertTrue(store.settings()['enabled'])
                finally:controller.close()

    def test_input_at_final_check_cancels_request_and_restores_management(self):
        with tempfile.TemporaryDirectory() as folder:
            store,clock,power,controller=self.make(folder)
            try:
                for instant in range(1,60):clock[0]=instant;controller.tick()
                entered=[]
                def inject_input():
                    entered.append(True)
                    power.idle=0
                power.inject=inject_input
                clock[0]=60;controller.tick()
                self.assertEqual(entered,[True])
                self.assertEqual(power.shutdowns,0)
                self.assertTrue(store.settings()['enabled'])
                self.assertEqual(controller.status.remaining,60)
            finally:controller.close()

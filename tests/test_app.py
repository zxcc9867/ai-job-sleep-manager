import tempfile
import unittest
from pathlib import Path
from sleep_manager.storage import Store
from sleep_manager.controller import Controller

class Power:
    dry_run=True
    def __init__(self): self.required=False; self.requests=0; self.idle=1000; self.closed=False
    def input_idle_seconds(self): return self.idle
    def set_required(self,value): self.required=value
    def suspend(self,before_suspend=None):
        if before_suspend and not before_suspend(): raise RuntimeError('cancelled')
        self.requests+=1
    def close(self): self.closed=True

class Reader:
    last_error=''
    def poll(self): return []

class ControllerTests(unittest.TestCase):
    def test_end_to_end_metadata_countdown_and_one_dry_request(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)); store.save_settings({'delay':60,'enabled':True})
            p=Power(); clock=[0]
            c=Controller(store,p,Reader(),clock=lambda:clock[0])
            store.append({'provider':'claude','session_id':'s','kind':'start'})
            self.assertEqual(c.tick().mode,'running'); self.assertTrue(p.required)
            store.append({'provider':'claude','session_id':'s','kind':'complete','metadata_complete':True})
            clock[0]=1; self.assertEqual(c.tick().remaining,60)
            for t in range(2,62):
                clock[0]=t; c.tick()
            self.assertEqual(p.requests,1)
            clock[0]=62; c.tick()
            self.assertEqual(p.requests,1)
            c.close(); self.assertTrue(p.closed)
    def test_restart_does_not_trust_saved_active_jobs(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)); store.append({'provider':'claude','session_id':'s','kind':'start'})
            c=Controller(store,Power(),Reader(),clock=lambda:0)
            self.assertEqual(c.tick().mode,'unknown')
    def test_final_check_drains_new_work(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)); store.save_settings({'delay':60})
            class RacingPower(Power):
                def suspend(self,before_suspend=None):
                    store.append({'provider':'claude','session_id':'new','kind':'start'})
                    return super().suspend(before_suspend)
            p=RacingPower(); clock=[0]; c=Controller(store,p,Reader(),clock=lambda:clock[0])
            store.append({'provider':'claude','session_id':'s','kind':'complete','metadata_complete':True})
            c.tick()
            for t in range(1,61):
                clock[0]=t; c.tick()
            self.assertIn('claude:new',c.engine.jobs)
            self.assertEqual(p.requests,0)
            self.assertTrue(p.required)
    def test_uncertain_session_end_does_not_arm_timer(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)); c=Controller(store,Power(),Reader(),clock=lambda:0)
            store.append({'provider':'claude','session_id':'s','kind':'session_end','metadata_complete':False})
            self.assertEqual(c.tick().mode,'unknown')



class FaultTests(unittest.TestCase):
    def test_preflight_failure_does_not_retry_every_tick(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)); store.save_settings({'delay':60})
            class FailingPower(Power):
                def suspend(self,before_suspend=None):
                    self.requests+=1
                    raise RuntimeError('preflight unavailable')
            p=FailingPower(); clock=[0]; c=Controller(store,p,Reader(),clock=lambda:clock[0])
            store.append({'provider':'claude','session_id':'s','kind':'complete','metadata_complete':True})
            c.tick()
            for t in range(1,64):
                clock[0]=t; c.tick()
            self.assertEqual(p.requests,1)

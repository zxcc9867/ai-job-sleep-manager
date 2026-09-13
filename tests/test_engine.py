import unittest

from sleep_manager.engine import Engine


def ev(kind, session='s', provider='codex', **extra):
    return dict(provider=provider, session_id=session, kind=kind, **extra)


class EngineTests(unittest.TestCase):
    def test_no_jobs_never_arms_sleep(self):
        e = Engine(delay=60)
        self.assertFalse(e.tick(1000, 1000).required)
        self.assertFalse(e.tick(2000, 2000).sleep_due)

    def test_active_job_protects_past_os_idle_timeout(self):
        e = Engine(delay=60)
        e.ingest(ev('start'), 0)
        self.assertEqual(e.tick(600, 600).mode, 'running')
        self.assertTrue(e.tick(600, 600).required)

    def test_last_job_completion_starts_full_delay(self):
        e = Engine(delay=60)
        e.ingest(ev('start'), 0)
        e.ingest(ev('start', 'b'), 0)
        e.ingest(ev('complete'), 5)
        self.assertIsNone(e.tick(20, 20).remaining)
        e.ingest(ev('complete', 'b'), 30)
        self.assertEqual(e.tick(30, 30).remaining, 60)
        self.assertFalse(e.tick(89, 89).sleep_due)
        self.assertTrue(e.tick(90, 90).sleep_due)

    def test_input_restarts_delay(self):
        e = Engine(delay=60)
        e.ingest(ev('start'), 0)
        e.ingest(ev('waiting'), 5)
        e.tick(5, 5)
        self.assertEqual(e.tick(55, 0).remaining, 60)
        self.assertFalse(e.tick(100, 45).sleep_due)
        self.assertTrue(e.tick(115, 60).sleep_due)

    def test_new_work_cancels_countdown(self):
        e = Engine(delay=60)
        e.ingest(ev('start'), 0)
        e.ingest(ev('complete'), 2)
        e.tick(2, 2)
        e.ingest(ev('start', 'new'), 61)
        self.assertEqual(e.tick(62, 62).mode, 'running')

    def test_child_outlives_parent(self):
        e = Engine(delay=60)
        e.ingest(ev('start'), 0)
        e.ingest(ev('child_start', task_id='child'), 1)
        e.ingest(ev('complete'), 2)
        self.assertEqual(e.tick(3, 3).mode, 'running')
        e.ingest(ev('child_stop', task_id='child'), 4)
        self.assertEqual(e.tick(4, 4).mode, 'countdown')

    def test_unknown_never_explicitly_sleeps_and_expires_protection(self):
        e = Engine(delay=60, recovery=100)
        e.ingest(ev('start'), 0)
        e.ingest(ev('unknown'), 5)
        self.assertTrue(e.tick(104, 104).required)
        s = e.tick(105, 105)
        self.assertEqual(s.mode, 'fault')
        self.assertFalse(s.required)
        self.assertFalse(s.sleep_due)

    def test_running_known_work_still_protected_when_another_unknown_expires(self):
        e = Engine(delay=60, recovery=100)
        e.ingest(ev('unknown', 'lost'), 0)
        e.ingest(ev('start', 'active'), 0)
        self.assertTrue(e.tick(200, 200).required)
        self.assertFalse(e.tick(200, 200).sleep_due)

    def test_pause_releases_and_resume_restarts_countdown(self):
        e = Engine(delay=60)
        e.ingest(ev('start'), 0)
        e.ingest(ev('complete'), 1)
        e.tick(1, 1)
        e.set_enabled(False, 20)
        self.assertFalse(e.tick(40, 40).required)
        e.set_enabled(True, 50)
        self.assertEqual(e.tick(50, 50).remaining, 60)

    def test_change_delay_restarts_timer(self):
        e = Engine(delay=60)
        e.ingest(ev('start'), 0)
        e.ingest(ev('complete'), 1)
        e.tick(1, 1)
        e.set_delay(120, 40)
        self.assertEqual(e.tick(40, 40).remaining, 120)

    def test_resume_never_reuses_expired_deadline(self):
        e = Engine(delay=60)
        e.ingest(ev('start'), 0)
        e.ingest(ev('complete'), 1)
        e.tick(1, 1)
        e.on_resume(10000)
        self.assertFalse(e.tick(10000, 10000).sleep_due)
        self.assertEqual(e.tick(10000, 10000).remaining, 60)

    def test_old_turn_completion_cannot_finish_new_turn(self):
        e = Engine(delay=60)
        e.ingest(ev('start', turn_id='a'), 0)
        e.ingest(ev('start', turn_id='b'), 10)
        e.ingest(ev('complete', turn_id='a'), 11)
        self.assertEqual(e.tick(12, 12).mode, 'running')

    def test_unconfirmed_background_blocks_sleep(self):
        e = Engine(delay=60)
        e.ingest(ev('start'), 0)
        e.ingest(ev('complete', background_active=True), 1)
        self.assertFalse(e.tick(100, 100).sleep_due)

    def test_repeated_completion_does_not_postpone_timer(self):
        e = Engine(delay=60)
        e.ingest(ev('start'), 0)
        e.ingest(ev('complete'), 10)
        e.tick(10, 10)
        e.ingest(ev('complete'), 50)
        self.assertTrue(e.tick(70, 70).sleep_due)

    def test_old_timestamp_ignored(self):
        e = Engine(delay=60)
        e.ingest(ev('start', timestamp=100), 0)
        e.ingest(ev('complete', timestamp=99), 1)
        self.assertEqual(e.tick(2, 2).mode, 'running')


class RegressionTests(unittest.TestCase):
    def test_unknown_parent_keeps_confirmed_child_running(self):
        e=Engine(delay=60,recovery=100)
        e.ingest(ev('unknown'),0)
        e.ingest(ev('child_start',task_id='child'),1)
        self.assertTrue(e.tick(200,200).required)
    def test_uncertain_child_stop_does_not_finish_child(self):
        e=Engine(delay=60,recovery=100)
        e.ingest(ev('start'),0)
        e.ingest(ev('child_start',task_id='child'),1)
        e.ingest(ev('complete'),2)
        e.ingest(ev('child_stop',task_id='child',metadata_complete=False),3)
        self.assertFalse(e.tick(99,99).sleep_due)
        self.assertEqual(e.tick(99,99).mode,'unknown')
    def test_final_log_overrides_late_stop_observer(self):
        e=Engine(delay=60)
        e.ingest(ev('start',turn_id='t',timestamp=100),0)
        e.ingest(ev('unknown',turn_id='t',timestamp=111),2)
        e.ingest(ev('complete',turn_id='t',timestamp=110,source='codex-local-log'),3)
        self.assertEqual(e.tick(3,3).mode,'countdown')
        e.ingest(ev('unknown',turn_id='t',timestamp=112,source='hook:Stop'),4)
        self.assertEqual(e.tick(4,4).mode,'countdown')

if __name__ == '__main__':
    unittest.main()


class OrderingTests(unittest.TestCase):
    def test_late_child_start_cannot_be_discarded_by_parent_clock(self):
        e=Engine(delay=60)
        e.ingest(ev('waiting',timestamp=20),0)
        e.ingest(ev('child_start',task_id='c',timestamp=19),1)
        self.assertEqual(e.tick(100,100).mode,'running')
    def test_old_turn_unknown_does_not_cancel_new_turn(self):
        e=Engine(delay=60)
        e.ingest(ev('start',turn_id='new',timestamp=100),0)
        e.ingest(ev('unknown',turn_id='old',timestamp=101),1)
        self.assertEqual(e.tick(1900,1900).mode,'running')

class ChildRecoveryTests(unittest.TestCase):
    def test_empty_authoritative_registry_resolves_uncertain_child(self):
        e=Engine(delay=60)
        e.ingest(ev('start',provider='claude'),0)
        e.ingest(ev('child_start',provider='claude',task_id='c'),1)
        e.ingest(ev('child_stop',provider='claude',task_id='c',metadata_complete=False),2)
        e.ingest(ev('complete',provider='claude',metadata_complete=True,background_active=False),3)
        self.assertEqual(e.tick(3,3).mode,'countdown')
    def test_late_uncertain_child_stop_does_not_undo_final_evidence(self):
        e=Engine(delay=60)
        e.ingest(ev('start'),0)
        e.ingest(ev('child_start',task_id='c'),1)
        e.ingest(ev('complete'),2)
        e.ingest(ev('child_stop',task_id='c',metadata_complete=True),3)
        e.ingest(ev('child_stop',task_id='c',metadata_complete=False),4)
        self.assertEqual(e.tick(4,4).mode,'countdown')


class DetectorFaultTests(unittest.TestCase):
    def test_reader_failure_after_completion_cancels_sleep(self):
        e=Engine(delay=60)
        e.ingest(ev('start',turn_id='t',timestamp=100),0)
        e.ingest(ev('complete',turn_id='t',timestamp=110,source='codex-local-log'),1)
        e.tick(1,1)
        e.ingest(ev('unknown',source='codex-local-log'),2)
        self.assertEqual(e.tick(62,62).mode,'unknown')
        self.assertFalse(e.tick(62,62).sleep_due)

import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from sleep_manager.storage import Store

class StorageTests(unittest.TestCase):
    def test_metadata_only_and_duplicate_event(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d))
            e={'provider':'codex','session_id':'s','kind':'start','prompt':'SECRET','timestamp':1}
            s.append(e,'same'); s.append(e,'same')
            rows=s.after(0)
            self.assertEqual(len(rows),1)
            self.assertNotIn('prompt',rows[0][1])
            self.assertNotIn('SECRET',repr(rows))
    def test_concurrent_hook_writes_not_lost(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); Store(root)
            def write(i):
                Store(root).append({'provider':'claude','session_id':str(i),'kind':'start'},str(i))
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(write,range(30)))
            self.assertEqual(len(Store(root).after(0)),30)
    def test_settings_survive_reopen(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); s=Store(root); s.save_settings({'delay':120,'enabled':False})
            self.assertEqual(Store(root).settings(),{'delay':120,'enabled':False})


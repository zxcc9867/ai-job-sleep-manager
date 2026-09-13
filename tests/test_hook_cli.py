import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from sleep_manager.storage import Store

APP=Path(__file__).resolve().parents[1]/'app.py'

class HookCLITests(unittest.TestCase):
    def test_real_hook_entrypoint_redacts_and_never_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            payload={'session_id':'s','hook_event_name':'Stop','background_tasks':[],'session_crons':[],'stop_hook_active':False,'last_assistant_message':'PRIVATE'}
            r=subprocess.run([sys.executable,str(APP),'--data-dir',d,'--hook','claude'],input=json.dumps(payload),capture_output=True,text=True,timeout=10)
            self.assertEqual(r.returncode,0)
            self.assertEqual(r.stdout,'')
            rows=Store(Path(d)).after(0)
            self.assertEqual(rows[0][1]['kind'],'complete')
            self.assertNotIn('PRIVATE',repr(rows))
    def test_invalid_input_never_blocks_ai_or_writes_payload(self):
        with tempfile.TemporaryDirectory() as d:
            r=subprocess.run([sys.executable,str(APP),'--data-dir',d,'--hook','claude'],input='not-json PRIVATE',capture_output=True,text=True,timeout=10)
            self.assertEqual(r.returncode,0)
            self.assertEqual(r.stdout,'')
            self.assertEqual(r.stderr,'')
            self.assertFalse((Path(d)/'events.sqlite3').exists())

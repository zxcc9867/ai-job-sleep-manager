import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from sleep_manager.storage import Store
from sleep_manager.controller import Controller
from test_app import Power,Reader

class ReadabilityPreferencesTests(unittest.TestCase):
    def test_text_size_persists_independently_of_power_settings(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d));store.save_settings(dict(delay=900,enabled=False,action='shutdown'))
            original=store.settings()
            self.assertEqual(store.text_size(),12)
            store.set_text_size(16)
            self.assertEqual(Store(Path(d)).text_size(),16)
            self.assertEqual(store.settings(),original)
            with self.assertRaises(ValueError): store.set_text_size(100)
            self.assertEqual(store.text_size(),16)

@unittest.skipUnless(__import__('os').name=='nt','Windows Tk UI')
class ReadabilityUITests(unittest.TestCase):
    def test_resize_and_font_change_preserve_job_selection_and_power_settings(self):
        from sleep_manager.ui import Window
        from tkinter import font
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d));c=Controller(store,Power(),Reader())
            c._accept(dict(provider='codex',session_id='s',kind='start'))
            w=Window(c,SimpleNamespace(smoke_ui=False,data_dir=Path(d)),[])
            w.root.withdraw();w.catalog=SimpleNamespace(lookup=lambda jobs:{'codex:s':dict(title='긴 작업 설명',latest_request='진행해줘')})
            try:
                w.render_jobs();w.table.selection_set('codex:s')
                before=c.engine.delay,c.engine.action,c.engine.enabled
                w.text_size.set('아주 크게');w.apply_text_size()
                self.assertEqual(store.text_size(),16)
                self.assertEqual((c.engine.delay,c.engine.action,c.engine.enabled),before)
                self.assertEqual(w.table.selection(),('codex:s',))
                self.assertIn('진행해줘',w.details.get('1.0','end'))
                self.assertGreaterEqual(font.Font(root=w.root,font=w.details.cget('font')).actual('size'),16)
                w.layout_panels(900)
                self.assertEqual(int(w.detail_panel.grid_info()['row']),1)
                w.layout_panels(2200)
                self.assertEqual(int(w.detail_panel.grid_info()['row']),0)
            finally:w.close()

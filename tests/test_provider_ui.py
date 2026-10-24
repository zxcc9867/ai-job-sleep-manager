"""Exercise mixed providers with an isolated Store and fake power only."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sleep_manager.controller import Controller
from sleep_manager.engine import Job
from sleep_manager.storage import Store
from test_app import Power, Reader


@unittest.skipUnless(os.name=='nt','Windows Tk UI')
class ProviderUITests(unittest.TestCase):
    def make(self,folder):
        from sleep_manager.ui import Window
        store=Store(Path(folder))
        store.save_settings(dict(enabled=False,action='shutdown',delay=300))
        controller=Controller(store,Power(),Reader())
        for provider,sid in (('codex','main'),('codex','child'),('claude','other'),('future-ai','new')):
            if provider in ('codex','claude'):
                controller._accept(dict(provider=provider,session_id=sid,kind='start'))
            else:
                # A future adapter is not implemented; exercise its display only.
                controller.engine.jobs[provider+':'+sid]=Job(provider,sid,state='running')
        with patch('sleep_manager.ui.startup.is_enabled',return_value=False):
            window=Window(controller,SimpleNamespace(smoke_ui=False,data_dir=Path(folder)),[])
        window.root.withdraw()
        window.catalog=SimpleNamespace(lookup=lambda jobs:{
            'codex:main':dict(title='메인 작업',project='project-one',latest_request='진행해줘',
                commentary='긴 진행 설명\n'*100,activity='회귀 테스트 실행 중'),
            'codex:child':dict(title='하위 구현',parent_id='main'),
            'claude:other':dict(title='다른 작업',project='project-two'),
        })
        window.claude_details=SimpleNamespace(lookup=lambda *args:{})
        window.claude_presence=SimpleNamespace(lookup=lambda **args:{},last_error='')
        window.render_jobs()
        return controller,window

    def test_ai_icons_names_and_project_are_visible_with_records_collapsed(self):
        with tempfile.TemporaryDirectory() as folder:
            controller,window=self.make(folder)
            try:
                self.assertFalse(window.show_records.get())
                for key,name in (('codex:main','Codex'),('claude:other','Claude Code'),('future-ai:new','future-ai')):
                    self.assertIn(name,window.table.item(key,'text'))
                    self.assertEqual(window.table.item(key,'text').count(name),1)
                    self.assertTrue(window.table.item(key,'image'))
                    window.table.selection_set(key);window.show_job_details()
                    self.assertIn(name,window.detail_provider.cget('text'))
                window.table.selection_set('codex:main');window.show_job_details()
                self.assertIn('project-one',window.detail_provider.cget('text'))
                self.assertTrue(window.details.get('1.0','end').startswith('현재 활동:'))
                self.assertEqual(window.table.parent('codex:child'),'codex:main')
                self.assertFalse(controller.engine.enabled)
                self.assertEqual(controller.power.requests,0)
            finally: window.close()

    def test_theme_and_text_size_refresh_images_without_changing_selection_or_work(self):
        with tempfile.TemporaryDirectory() as folder:
            controller,window=self.make(folder)
            try:
                window.table.selection_set('codex:child');window.table.item('codex:main',open=False)
                before=window.table.item('codex:child','image')
                settings=controller.store.settings()
                window.theme_choice.set('다크 모드');window.apply_theme()
                after=window.table.item('codex:child','image')
                self.assertNotEqual(before,after)
                self.assertTrue(window.root.tk.call('image','width',after[0]))
                self.assertEqual(window.table.selection(),('codex:child',))
                self.assertFalse(window.table.item('codex:main','open'))
                window.text_size.set('아주 크게');window.apply_text_size()
                self.assertNotEqual(after,window.table.item('codex:child','image'))
                self.assertEqual(settings,controller.store.settings())
                self.assertFalse(controller.engine.enabled)
                self.assertEqual(controller.power.requests,0)
            finally: window.close()

    def test_same_job_refresh_retains_scroll_but_new_selection_starts_at_top(self):
        with tempfile.TemporaryDirectory() as folder:
            controller,window=self.make(folder)
            try:
                window.root.deiconify();window.root.geometry('1100x740')
                window.table.selection_set('codex:main');window.show_job_details()
                window.root.update_idletasks()
                window.details.yview_moveto(.5)
                position=window.details.yview()[0]
                self.assertGreater(position,.1)
                window.job_rows['codex:main']['current_activity']='파일 변경 확인 중'
                window.show_job_details();window.root.update_idletasks()
                self.assertAlmostEqual(position,window.details.yview()[0],delta=.015)
                window.table.selection_set('claude:other');window.show_job_details()
                window.root.update_idletasks()
                self.assertLess(window.details.yview()[0],.01)
                self.assertFalse(controller.engine.enabled)
                self.assertEqual(controller.power.requests,0)
            finally: window.close()

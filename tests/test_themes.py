"""Theme changes use an isolated DB and fake power; never alter PC power."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from sleep_manager.storage import Store
from sleep_manager.controller import Controller
from test_app import Power, Reader


class ThemePreferencesTests(unittest.TestCase):
    def test_theme_survives_font_changes_restart_and_existing_database(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with sqlite3.connect(root/'events.sqlite3') as db:
                db.execute('CREATE TABLE ui_preferences (id INTEGER PRIMARY KEY, font_size INTEGER NOT NULL)')
                db.execute('INSERT INTO ui_preferences VALUES (1,16)')
            db.close()
            store = Store(root)
            store.save_settings(dict(enabled=False, delay=300, action='shutdown'))
            store.save_execution_dry_run(False)
            before = store.settings()
            self.assertEqual(store.theme(), 'light')
            store.save_theme('dark')
            store.set_text_size(14)
            restored = Store(root)
            self.assertEqual(restored.theme(), 'dark')
            self.assertEqual(restored.text_size(), 14)
            self.assertEqual(restored.settings(), before)
            self.assertFalse(restored.execution_dry_run())
            with self.assertRaises(ValueError): store.save_theme('unknown')
            self.assertEqual(store.theme(), 'dark')


@unittest.skipUnless(os.name == 'nt', 'Windows Tk UI')
class ThemeUITests(unittest.TestCase):
    def make(self, folder):
        from sleep_manager.ui import Window
        store = Store(Path(folder))
        store.save_settings(dict(enabled=False, delay=300, action='shutdown'))
        controller = Controller(store, Power(), Reader())
        controller._accept(dict(provider='codex', session_id='theme-job', kind='start'))
        window = Window(controller, SimpleNamespace(smoke_ui=False, data_dir=Path(folder)), [])
        window.root.withdraw()
        window.catalog = SimpleNamespace(lookup=lambda jobs: {'codex:theme-job': dict(title='UI 변경', latest_request='다크 모드 추가')})
        window.render_jobs()
        return controller, window

    def test_live_switch_updates_main_and_open_diagnostics_preserving_work_and_draft(self):
        from sleep_manager.themes import PALETTES
        with tempfile.TemporaryDirectory() as folder:
            controller, window = self.make(folder)
            try:
                window.table.selection_set('codex:theme-job')
                window.show_job_details()
                window.minutes.set('7')
                window.toggle_settings()
                window.settings_tabs.select(2)
                window.show_diagnostics(dict(settings=controller.store.settings(), last_error=''))
                popup=window.root.tk.call('ttk::combobox::PopdownWindow',str(window.theme_select))
                before = controller.store.settings()
                detail = window.details.get('1.0', 'end')
                window.root.deiconify();window.root.update_idletasks()
                for name, label in [('dark', '다크 모드'), ('light', '밝은 모드'), ('dark', '다크 모드')]:
                    palette = PALETTES[name]
                    window.theme_choice.set(label)
                    window.theme_select.event_generate('<<ComboboxSelected>>')
                    self.assertEqual(window.root.cget('bg'), palette.bg)
                    self.assertEqual(window.details.cget('fg'), palette.text)
                    self.assertEqual(window.diagnostics_window.cget('bg'), palette.card)
                    self.assertEqual(window.diagnostic_details.cget('bg'), palette.inset)
                    self.assertEqual(window.details.cget('selectbackground'), palette.selected)
                    self.assertEqual(window.pause.cget('fg'), palette.primary_text)
                    self.assertEqual(window.root.tk.call(popup+'.f.l','cget','-background'),palette.inset)
                    window.refresh_button.event_generate('<Enter>')
                    self.assertEqual(window.refresh_button.cget('bg'),palette.secondary_hover)
                    window.refresh_button.event_generate('<Leave>')
                    self.assertEqual(window.refresh_button.cget('bg'),palette.card)
                    self.assertEqual(window.style.lookup('Treeview', 'background'), palette.card)
                    self.assertEqual(window.style.lookup('TNotebook', 'bordercolor'), palette.border)
                    self.assertEqual(window.table.tag_configure('unknown')['foreground'], palette.amber)
                    self.assertEqual(window.table.selection(), ('codex:theme-job',))
                    self.assertEqual(window.details.get('1.0', 'end'), detail)
                    self.assertEqual(window.minutes.get(), '7')
                    self.assertTrue(window.settings_open)
                    self.assertEqual(window.settings_tabs.index('current'), 2)
                    self.assertEqual(controller.store.settings(), before)
                    self.assertFalse(controller.engine.enabled)
                    self.assertEqual(controller.engine.jobs['codex:theme-job'].state, 'running')
                    self.assertEqual(controller.power.requests, 0)
                self.assertEqual(Store(Path(folder)).theme(), 'dark')
            finally: window.close()
            controller, restored = self.make(folder)
            try:
                self.assertEqual(restored.root.cget('bg'), PALETTES['dark'].bg)
                self.assertEqual(restored.theme_choice.get(), '다크 모드')
            finally: restored.close()

    def test_failed_theme_save_keeps_display_and_saved_preference(self):
        with tempfile.TemporaryDirectory() as folder:
            controller, window = self.make(folder)
            before = window.root.cget('bg')
            try:
                with patch.object(controller.store, 'save_theme', side_effect=sqlite3.OperationalError('locked')):
                    window.theme_choice.set('다크 모드')
                    window.theme_select.event_generate('<<ComboboxSelected>>')
                self.assertEqual(window.root.cget('bg'), before)
                self.assertEqual(window.theme_choice.get(), '밝은 모드')
                self.assertEqual(controller.store.theme(), 'light')
                self.assertIn('저장하지 못', window.theme_feedback.cget('text'))
                self.assertFalse(controller.engine.enabled)
            finally: window.close()

    def check_posted_dropdown(self,selection_event):
        from sleep_manager.themes import PALETTES
        with tempfile.TemporaryDirectory() as folder:
            controller,window=self.make(folder)
            errors=[]
            window.root.report_callback_exception=lambda *args:errors.append(args)
            window.claude_details=SimpleNamespace(lookup=lambda *args:{})
            window.claude_presence=SimpleNamespace(lookup=lambda:{})
            try:
                window.toggle_settings();window.settings_tabs.select(2)
                window.root.deiconify();window.root.update()
                original=controller.store.settings()
                for index,name in ((1,'dark'),(0,'light'),(1,'dark')):
                    window.root.tk.call('ttk::combobox::Post',str(window.theme_select))
                    window.root.update()
                    popup=window.root.tk.call('ttk::combobox::PopdownWindow',str(window.theme_select))
                    listbox=popup+'.f.l'
                    window.root.tk.call(listbox,'selection','clear',0,'end')
                    window.root.tk.call(listbox,'selection','set',index)
                    window.root.tk.call(listbox,'activate',index)
                    window.root.tk.call('event','generate',listbox,selection_event)
                    window.root.update()
                    self.assertEqual(errors,[])
                    self.assertEqual(window.root.cget('bg'),PALETTES[name].bg,
                        dict(choice=window.theme_choice.get(),saved=controller.store.theme(),feedback=window.theme_feedback.cget('text')))
                    self.assertEqual(window.details.cget('bg'),PALETTES[name].card)
                    self.assertEqual(controller.store.theme(),name)
                    self.assertIn('적용됨',window.theme_feedback.cget('text'))
                    self.assertEqual(controller.store.settings(),original)
                    self.assertEqual(controller.power.requests,0)
            finally: window.close()

    def test_mouse_selection_from_posted_dropdown_repaints_without_restart(self):
        self.check_posted_dropdown('<ButtonRelease-1>')

    def test_keyboard_selection_from_posted_dropdown_repaints_without_restart(self):
        self.check_posted_dropdown('<Return>')


class PaletteContrastTests(unittest.TestCase):
    def test_both_themes_keep_text_and_selection_readable(self):
        from sleep_manager.themes import PALETTES
        def luminance(color):
            values = [int(color[i:i+2], 16)/255 for i in (1, 3, 5)]
            return sum((v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4)*w
                       for v, w in zip(values, (.2126, .7152, .0722)))
        for name, p in PALETTES.items():
            for fg, bg in [(p.text,p.bg),(p.text,p.card),(p.muted,p.bg),(p.muted,p.card),
                           (p.accent,p.card),(p.amber,p.card),(p.error,p.card),(p.success,p.card),
                           (p.text,p.selected),(p.primary_text,p.primary),(p.primary_text,p.primary_hover)]:
                with self.subTest(theme=name, foreground=fg, background=bg):
                    a, b = sorted([luminance(fg), luminance(bg)], reverse=True)
                    self.assertGreaterEqual((a+.05)/(b+.05), 4.5)

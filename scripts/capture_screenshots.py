"""Capture the actual Tk UI with isolated example events; never touch power or AI settings.

Run on Windows with a visible desktop: python scripts/capture_screenshots.py
Pillow is required only for this documentation tool.
"""
from pathlib import Path
from types import SimpleNamespace
import sys
import ctypes
from ctypes import wintypes
import tempfile
import json
import time
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import ImageGrab
from sleep_manager.controller import Controller
from sleep_manager.storage import Store
from sleep_manager.ui import Window
from sleep_manager.claude_details import ClaudeDetails


class PreviewPower:
    dry_run = True

    def diagnostics(self):
        return dict(request_active=False)

    def input_idle_seconds(self):
        return 3600

    def set_required(self, required):
        pass

    def process_names(self):
        return {'codex.exe', 'claude.exe'}

    def suspend(self, before_suspend=None):
        raise AssertionError('Documentation preview must never reach suspend')

    def close(self):
        pass


class PreviewCatalog:
    def lookup(self,jobs):
        return {
            'codex:demo-codex-refactoring': dict(title='로그인 오류 수정',project='web-dashboard',latest_request='진행해줘',activity='터미널 명령 실행 중',activity_at=datetime.now(timezone.utc).isoformat(),commentary='세션 만료 처리를 수정했습니다. 지금은 로그인 회귀 테스트로 재발 여부를 확인하고 있습니다.',commentary_at=datetime.now(timezone.utc).isoformat(),plan=[
                dict(step='오류 원인 확인',status='completed'),
                dict(step='세션 만료 처리 수정',status='completed'),
                dict(step='회귀 테스트 실행',status='in_progress')]),
            'claude:demo-claude-tests': dict(title='API 회귀 테스트 추가',project='api-server'),
            'codex:demo-codex-review': dict(title='검토 작업 · Mencius',project='web-dashboard',parent_id='demo-codex-refactoring',commentary='로그인 수정 코드에서 세션 만료와 재로그인 경계 조건을 검토하고 있습니다.',commentary_at=datetime.now(timezone.utc).isoformat(),activity='파일 조회 도구 실행 중',activity_at=datetime.now(timezone.utc).isoformat()),
        }


class PreviewReader:
    last_error = ''

    def poll(self):
        return []


def capture(scenario, output, theme='light'):
    with tempfile.TemporaryDirectory(prefix='ai-sleep-preview-') as folder:
        store=Store(Path(folder))
        store.save_theme(theme)
        power=PreviewPower()
        if scenario in ('recovery','paused','failure'):
            store.save_execution_dry_run(False)
            power.dry_run=False  # PreviewPower is a fake; no native power calls.
        if scenario=='failure':
            from sleep_manager.power_state import PowerActionState
            store.save_settings(dict(enabled=False,action='shutdown',delay=300))
            store.save_power_action(PowerActionState('failed','legacy_shutdown_failure',
                'Previous failure in an older version').payload())
        controller = Controller(store, power, PreviewReader())
        sessions = [('codex', 'demo-codex-refactoring'), ('claude', 'demo-claude-tests'), ('codex', 'demo-codex-review')]
        for provider, session in sessions:
            controller._accept(dict(provider=provider, session_id=session, kind='start'))
        if scenario in ('countdown','unknown'):
            controller._accept(dict(provider='codex', session_id=sessions[2][1], kind='waiting'))
        if scenario == 'shutdown':
            controller.set_action('shutdown')
            for provider, session in sessions:
                controller._accept(dict(provider=provider, session_id=session, kind='complete'))
            controller.engine.baseline = time.monotonic() - 135
        elif scenario == 'countdown':
            for provider, session in sessions[:2]:
                controller._accept(dict(provider=provider, session_id=session, kind='complete'))
            controller.engine.baseline = time.monotonic() - 135
        elif scenario == 'paused':
            controller.set_enabled(False)
        elif scenario == 'unknown':
            for provider, session in sessions[:2]:
                controller._accept(dict(provider=provider, session_id=session, kind='complete'))
            controller._accept(dict(provider='codex', session_id=sessions[0][1], kind='unknown'))
        elif scenario == 'recovery':
            for provider, session in (sessions[0],sessions[2]):
                controller._accept(dict(provider=provider,session_id=session,kind='complete'))
            controller._accept(dict(provider='claude',session_id='demo-claude-tests',kind='interrupted'))
            controller.restored_jobs.add('claude:demo-claude-tests')
        args = SimpleNamespace(smoke_ui=False, screenshot=None, data_dir=Path(folder))
        window = Window(controller, args, [sys.executable, 'app.py'])
        window.catalog=PreviewCatalog()
        transcript=Path(folder)/'projects'/'demo'/'demo-claude-tests.jsonl'
        transcript.parent.mkdir(parents=True)
        stamp=datetime.now(timezone.utc).isoformat()
        messages=[dict(type='user',sessionId='demo-claude-tests',timestamp=stamp,message=dict(role='user',content='API 오류 처리를 수정하고 회귀 테스트를 추가해줘')),
            dict(type='assistant',sessionId='demo-claude-tests',timestamp=stamp,message=dict(role='assistant',content=[dict(type='text',text='오류 응답 처리를 수정했습니다. 지금은 잘못된 입력과 인증 만료 상황의 회귀 테스트를 실행하고 있습니다.'),dict(type='tool_use',id='demo-call',name='Bash')]))]
        transcript.write_text(''.join(json.dumps(m,ensure_ascii=False)+'\n' for m in messages),encoding='utf-8')
        window.claude_details=ClaudeDetails(Path(folder)/'projects')
        window.root.geometry('+30+20')
        if scenario in ('shutdown','recovery'): window.toggle_list_options()
        if scenario=='shutdown': window.show_complete.set(True)
        if scenario=='recovery': window.show_history.set(True)
        if scenario in ('settings','large_text','settings_unsaved','detection','appearance'): window.toggle_settings()
        if scenario=='detection': window.settings_tabs.select(1)
        if scenario=='appearance': window.settings_tabs.select(2)
        if scenario=='diagnostics':
            from sleep_manager import __version__
            controller.diagnostics=lambda:dict(version=__version__,pid=4321,run_id='example-run',
                database_path='C:/Example/AIJobSleepManager/events.sqlite3',settings=store.settings(),
                shutdown_permission=dict(available=True,error_code=0),power_action={},last_error='')
            window.diagnose()
        if scenario in ('settings','settings_unsaved'):
            window.minutes.set('5');window.action.set('정상 종료')
            window.apply_delay()
            if scenario=='settings_unsaved': window.minutes.set('7')
        if scenario=='large_text':
            window.text_size.set('아주 크게'); window.apply_text_size()
            window.root.geometry('1000x900+30+20')
        window.root.lift()
        window.root.attributes('-topmost', True)
        if scenario=='diagnostics':
            window.diagnostics_window.attributes('-topmost',True)
            window.diagnostics_window.lift()
        if scenario=='activity': window.root.geometry(f'1080x{min(740,window.root.winfo_screenheight()-80)}+30+20')

        def save():
            window.render_jobs()
            if scenario in ('running','activity','settings'):
                window.table.selection_set('codex:demo-codex-refactoring')
                window.show_job_details()
            window.root.update()
            if scenario=='recovery':
                window.table.selection_set('claude:demo-claude-tests')
                window.show_job_details();window.root.update()
                assert '재부팅' in window.details.get('1.0','end')
            if scenario=='claude':
                window.table.selection_set('claude:demo-claude-tests')
                window.show_job_details();window.root.update()
                assert 'API 오류 처리를 수정' in window.details.get('1.0','end')
            if scenario=='activity':
                shell=window.root.nametowidget(window.page_canvas.itemcget(window.page_window,'window'))
                position=window.detail_panel.winfo_rooty()-shell.winfo_rooty()-16
                window.page_canvas.yview_moveto(max(0,position)/max(1,shell.winfo_height()))
                window.root.update()
            if scenario in ('running','activity','settings'):
                assert window.table.selection()==('codex:demo-codex-refactoring',)
                assert '최근 지시: 진행해줘' in window.details.get('1.0','end')
            # Allow the Windows compositor to paint the updated selection before capture.
            time.sleep(.2)
            # DWM bounds are physical pixels even when Tk uses DPI-virtualized coordinates.
            target=window.diagnostics_window if scenario=='diagnostics' else window.root
            hwnd=ctypes.windll.user32.GetParent(target.winfo_id())
            rect=wintypes.RECT()
            result=ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd,9,ctypes.byref(rect),ctypes.sizeof(rect))
            if result: raise OSError('Cannot obtain physical window bounds')
            ImageGrab.grab(bbox=(rect.left,rect.top,rect.right,rect.bottom)).save(output)
            window.close()

        window.root.after(1100, save)
        window.run()
        print(output.name)


if __name__ == '__main__':
    destination = Path(__file__).resolve().parents[1] / 'docs' / 'images'
    destination.mkdir(parents=True, exist_ok=True)
    for scenario in ('running', 'countdown', 'paused', 'failure', 'unknown', 'shutdown', 'activity', 'settings', 'claude','recovery','detection','diagnostics'):
        capture(scenario, destination / (scenario + '.png'))
    for scenario,filename in [('running','dark'),('appearance','dark-settings'),('diagnostics','dark-diagnostics')]:
        capture(scenario,destination/(filename+'.png'),theme='dark')

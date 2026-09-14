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
import time
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import ImageGrab
from sleep_manager.controller import Controller
from sleep_manager.storage import Store
from sleep_manager.ui import Window


class PreviewPower:
    dry_run = True

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


def capture(scenario, output):
    with tempfile.TemporaryDirectory(prefix='ai-sleep-preview-') as folder:
        controller = Controller(Store(Path(folder)), PreviewPower(), PreviewReader())
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
        args = SimpleNamespace(smoke_ui=False, screenshot=None, data_dir=Path(folder))
        window = Window(controller, args, [sys.executable, 'app.py'])
        window.catalog=PreviewCatalog()
        window.root.geometry('+30+20')
        if scenario=='shutdown': window.show_complete.set(True)
        if scenario=='settings': window.toggle_settings()
        window.root.lift()
        window.root.attributes('-topmost', True)
        if scenario=='activity': window.root.geometry(f'1080x{min(740,window.root.winfo_screenheight()-80)}+30+20')

        def save():
            window.render_jobs()
            if scenario in ('running','activity','settings'):
                window.table.selection_set('codex:demo-codex-refactoring')
                window.show_job_details()
            window.root.update()
            if scenario=='activity':
                window.page_canvas.yview_moveto(0)
                window.root.update()
            if scenario in ('running','activity','settings'):
                assert window.table.selection()==('codex:demo-codex-refactoring',)
                assert '최근 지시: 진행해줘' in window.details.get('1.0','end')
            # Allow the Windows compositor to paint the updated selection before capture.
            time.sleep(.2)
            # DWM bounds are physical pixels even when Tk uses DPI-virtualized coordinates.
            hwnd=ctypes.windll.user32.GetParent(window.root.winfo_id())
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
    for scenario in ('running', 'countdown', 'paused', 'unknown', 'shutdown', 'activity', 'settings'):
        capture(scenario, destination / (scenario + '.png'))

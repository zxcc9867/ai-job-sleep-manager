"""Capture the actual Tk UI with isolated example events; never touch power or AI settings.

Run on Windows with a visible desktop: python scripts/capture_screenshots.py
Pillow is required only for this documentation tool.
"""
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import time

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
        controller._accept(dict(provider='codex', session_id=sessions[2][1], kind='waiting'))
        if scenario == 'countdown':
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
        window.root.geometry('990x800+30+30')
        window.root.lift()
        window.root.attributes('-topmost', True)

        def save():
            window.root.update()
            x, y = window.root.winfo_rootx(), window.root.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + window.root.winfo_width(), y + window.root.winfo_height())).save(output)
            window.close()

        window.root.after(1100, save)
        window.run()
        print(output.name)


if __name__ == '__main__':
    destination = Path(__file__).resolve().parents[1] / 'docs' / 'images'
    destination.mkdir(parents=True, exist_ok=True)
    for scenario in ('running', 'countdown', 'paused', 'unknown'):
        capture(scenario, destination / (scenario + '.png'))

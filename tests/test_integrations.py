import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


class IntegrationTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.find_spec('sleep_manager.integrations')
        self.assertIsNotNone(spec, 'integration module must implement the adapters')
        from sleep_manager import integrations
        return integrations

    def event(self, provider='claude', event='Stop', **kwargs):
        return self.module().normalize_hook(provider, dict(session_id='s-1', hook_event_name=event, **kwargs))

    def test_only_minimum_metadata_survives(self):
        before = time.time()
        result = self.event('codex', 'UserPromptSubmit', turn_id='t1', prompt='secret', cwd='private', transcript_path='private')
        self.assertEqual({k: v for k, v in result.items() if k != 'timestamp'}, dict(provider='codex', session_id='s-1', kind='start', task_id='', turn_id='t1', metadata_complete=True, source='hook:UserPromptSubmit'))
        self.assertGreaterEqual(result['timestamp'], before)

    def test_codex_stop_cannot_claim_background_completion(self):
        result = self.event('codex', stop_hook_active=False)
        self.assertEqual(result['kind'], 'unknown')
        self.assertFalse(result['metadata_complete'])

    def test_claude_stop_requires_registry_and_no_continuation(self):
        for payload in ({}, {'background_tasks': []}, {'background_tasks': [], 'session_crons': [], 'stop_hook_active': True}):
            with self.subTest(payload=payload):
                self.assertEqual(self.event(**payload)['kind'], 'unknown')
        result = self.event(background_tasks=[], session_crons=[], stop_hook_active=False)
        self.assertEqual(result['kind'], 'complete')
        self.assertTrue(result['metadata_complete'])
        self.assertFalse(result['background_active'])
        result = self.event(background_tasks=[{'id': 'b', 'status': 'running', 'command': 'secret'}], session_crons=[], stop_hook_active=False)
        self.assertEqual(result['kind'], 'running')
        self.assertTrue(result['background_active'])

    def test_scheduled_continuation_is_not_complete(self):
        result = self.event(background_tasks=[], session_crons=[{'id': 'cron'}], stop_hook_active=False)
        self.assertEqual(result['kind'], 'unknown')

    def test_actual_pending_notification_and_resume(self):
        for notification in ['permission_prompt', 'elicitation_dialog', 'elicitation_url_dialog']:
            self.assertEqual(self.event(event='Notification', notification_type=notification)['kind'], 'waiting')
        for notification in ['idle_prompt', 'auth_success', 'agent_completed', 'agent_needs_input']:
            self.assertIsNone(self.event(event='Notification', notification_type=notification))
        for provider in ['claude', 'codex']:
            self.assertEqual(self.event(provider, 'PermissionRequest')['kind'], 'unknown')
            self.assertEqual(self.event(provider, 'PreToolUse')['kind'], 'running')
            self.assertEqual(self.event(provider, 'PostToolUse')['kind'], 'running')
        self.assertEqual(self.event(event='ElicitationResult')['kind'], 'running')

    def test_children_keep_distinct_ids(self):
        for provider in ['claude', 'codex']:
            start = self.event(provider, 'SubagentStart', agent_id='child1')
            stop = self.event(provider, 'SubagentStop', agent_id='child1', stop_hook_active=False)
            self.assertEqual((start['kind'], start['task_id']), ('child_start', 'child1'))
            self.assertEqual((stop['kind'], stop['task_id']), ('child_stop', 'child1'))
            self.assertFalse(stop['metadata_complete'])

    def test_malformed_inputs_are_ignored_or_unknown(self):
        module = self.module()
        for provider, payload in [('other', {}), ('codex', []), ('claude', {'hook_event_name': 'Stop'}), ('claude', {'session_id': 's', 'hook_event_name': []})]:
            self.assertIsNone(module.normalize_hook(provider, payload))
        self.assertEqual(self.event(background_tasks='bad', session_crons=[], stop_hook_active=False)['kind'], 'unknown')
        self.assertEqual(self.event(event='PostToolUseFailure')['kind'], 'running')
        self.assertEqual(self.event(event='StopFailure')['kind'], 'unknown')

    def test_install_preserves_existing_hooks_backups_and_is_idempotent(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / '.claude/settings.json'
            path.parent.mkdir()
            original = {'permissions': {'allow': ['Read']}, 'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'echo keep'}]}]}}
            path.write_text(json.dumps(original), encoding='utf-8')
            backups = module.install_hooks(home, [sys.executable, 'C:/example/app.py'])
            self.assertEqual(len(backups), 1)
            self.assertEqual(json.loads(Path(backups[0]).read_text(encoding='utf-8')), original)
            installed = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(installed['permissions'], original['permissions'])
            self.assertIn(original['hooks']['Stop'][0], installed['hooks']['Stop'])
            self.assertEqual(module.install_hooks(home, [sys.executable, 'C:/example/app.py']), [])
            module.install_hooks(home, [sys.executable, 'C:/example/app.py'], remove=True)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), original)
            self.assertEqual(module.install_hooks(home, [sys.executable, 'C:/example/app.py'], remove=True), [])

    def test_invalid_second_config_prevents_first_mutation(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / '.codex').mkdir()
            path = home / '.codex/hooks.json'
            path.write_text('{"hooks":[]}', encoding='utf-8')
            with self.assertRaises(ValueError):
                module.install_hooks(home, [sys.executable, 'app.py'])
            self.assertFalse((home / '.claude/settings.json').exists())
            self.assertEqual(path.read_text(), '{"hooks":[]}')

    def test_installed_commands_deliver_stdin_and_literal_arguments(self):
        module = self.module()
        with tempfile.TemporaryDirectory(prefix='hook args ') as directory:
            home = Path(directory)
            script = home / "probe ' dollar $.py"
            script.write_text('import sys,json; print(json.dumps([sys.argv[1:],sys.stdin.read()]))', encoding='utf-8')
            module.install_hooks(home, [sys.executable, str(script)])
            for provider, relative in [('claude', '.claude/settings.json'), ('codex', '.codex/hooks.json')]:
                hook = json.loads((home / relative).read_text(encoding='utf-8'))['hooks']['Stop'][0]['hooks'][0]
                command = [hook['command'], *hook['args']] if provider == 'claude' else hook['commandWindows']
                if provider == 'codex' and os.name != 'nt':
                    continue
                result = subprocess.run(command, input='{"test":true}', text=True, capture_output=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), [['--hook', provider], '{"test":true}'])


if __name__ == '__main__':
    unittest.main()

class InstallerSafetyTests(unittest.TestCase):
    def test_duplicate_keys_preserved_instead_of_silently_discarded(self):
        from sleep_manager.integrations import install_hooks
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / '.claude/settings.json'
            path.parent.mkdir()
            original = '{"permissions":{"allow":["Read"]},"permissions":{"deny":["Bash"]}}'
            path.write_text(original, encoding='utf-8')
            with self.assertRaises(ValueError):
                install_hooks(home, [sys.executable, 'app.py'])
            self.assertEqual(path.read_text(encoding='utf-8'), original)

    def test_remove_preserves_modified_and_other_installation_handlers(self):
        from sleep_manager.integrations import install_hooks
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            prefix = [sys.executable, 'C:/first/app.py']
            install_hooks(home, prefix)
            path = home / '.claude/settings.json'
            data = json.loads(path.read_text(encoding='utf-8'))
            edited = data['hooks']['Stop'][0]['hooks'][0]
            edited['args'].append('--someone-elses-option')
            path.write_text(json.dumps(data), encoding='utf-8')
            install_hooks(home, prefix, remove=True)
            remaining = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(remaining['hooks']['Stop'][0]['hooks'][0], edited)

    def test_invalid_provider_value_does_not_raise(self):
        from sleep_manager.integrations import normalize_hook
        self.assertIsNone(normalize_hook([], {'session_id': 's', 'hook_event_name': 'Stop'}))

if __name__ == '__main__':
    unittest.main()



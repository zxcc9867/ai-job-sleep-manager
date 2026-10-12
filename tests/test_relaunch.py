import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class RelaunchTests(unittest.TestCase):
    def test_hidden_uac_helper_waits_for_parent_and_preserves_exact_data_path(self):
        from sleep_manager.relaunch import request_admin_relaunch
        calls = []
        def shell(*args):
            calls.append(args)
            return 33
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/"data with spaces ' quoted"
            with patch('sleep_manager.relaunch._shell_execute', side_effect=shell):
                self.assertTrue(request_admin_relaunch(target, activate=True))
        args = calls[0]
        self.assertEqual(args[1:3], ('runas', 'powershell.exe'))
        self.assertEqual(args[-1], 0)
        encoded = args[3].split('-EncodedCommand ')[1]
        script = base64.b64decode(encoded).decode('utf-16le')
        self.assertIn('Get-Process -Id', script)
        self.assertIn('--activate-power-control', script)
        self.assertIn(str(target).replace("'", "''"), script)
        self.assertNotIn('shutdown.exe', script)

    def test_cancelled_uac_returns_false(self):
        from sleep_manager.relaunch import request_admin_relaunch
        with patch('sleep_manager.relaunch._shell_execute', return_value=5):
            self.assertFalse(request_admin_relaunch(Path('C:/example'), activate=True))

    def test_startup_forced_observation_does_not_require_elevation(self):
        from app import requires_elevation
        self.assertFalse(requires_elevation(saved_dry=False, force_observe=True, activate=False, admin=False))
        self.assertTrue(requires_elevation(saved_dry=True, force_observe=False, activate=True, admin=False))
        self.assertTrue(requires_elevation(saved_dry=False, force_observe=False, activate=False, admin=False))
        self.assertFalse(requires_elevation(saved_dry=False, force_observe=False, activate=False, admin=True))

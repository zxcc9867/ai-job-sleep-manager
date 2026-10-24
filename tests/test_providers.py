"""Provider identity never changes lifecycle or power decisions."""
import unittest
from unittest.mock import patch

from sleep_manager.engine import Job
from sleep_manager.job_view import job_row
from sleep_manager.providers import GENERIC, PROVIDERS, ProviderIdentity, provider_identity


class ProviderIdentityTests(unittest.TestCase):
    def test_unknown_provider_is_named_without_becoming_claude(self):
        row=job_row(Job('future-ai','session',state='running'),{},'shutdown')
        self.assertIn('future-ai',row['tool'])
        self.assertNotIn('Claude',row['tool'])
        self.assertEqual(row['provider'],'future-ai')
        self.assertEqual(provider_identity('future-ai').glyph,GENERIC)
        self.assertTrue(row['pending'])

    def test_new_display_identity_requires_only_registry_entry(self):
        identity=ProviderIdentity('Example AI',GENERIC,'#234567','#abcdef')
        with patch.dict(PROVIDERS,{'example':identity}):
            self.assertEqual(job_row(Job('example','s'),{},'sleep')['tool'],'Example AI')
            self.assertIs(provider_identity('example'),identity)

    def test_uuid_title_uses_observed_instruction_and_preserves_original(self):
        title='01a1190e-798c-73f1-8068-9a3c5ebad123'
        row=job_row(Job('codex','s'),dict(title=title,latest_request='오른쪽 패널의 관계 요약을 구현해줘'),'sleep')
        self.assertEqual(row['title'],'오른쪽 패널의 관계 요약을 구현해줘')
        self.assertEqual(row['original_title'],title)
        custom=job_row(Job('codex','s'),dict(title=title,label='나의 구현 작업',latest_request='진행해줘'),'sleep')
        self.assertEqual(custom['title'],'나의 구현 작업')

    def test_identity_comes_from_job_not_untrusted_display_metadata(self):
        row=job_row(Job('codex','s'),dict(tool='Claude Code',provider='claude'),'sleep')
        self.assertEqual(row['tool'],'Codex')
        self.assertEqual(row['provider'],'codex')

    def test_blank_and_control_character_provider_names_are_readable(self):
        self.assertEqual(provider_identity('').name,'알 수 없는 AI')
        self.assertNotIn('\n',provider_identity('future\nAI').name)
        self.assertEqual(provider_identity('claude').name,'Claude Code')

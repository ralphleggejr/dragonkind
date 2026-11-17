import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeoutError
from dragonbot.cooldowns import CooldownEvidenceError, cooldown_evidence
from dragonbot.__main__ import account_ready_for_story, confirm_account_cooldown
from dragonbot.core import load_state


class CooldownParsing(unittest.TestCase):
    def observation(self, timestamps):
        return {'label': 'You can brave Threshing again in', 'countdown_datetimes': timestamps,
                'countdown_text': '00:43:20'}

    def test_site_deadline_is_used_instead_of_an_invented_four_hour_deadline(self):
        evidence = cooldown_evidence(self.observation(['1970-01-01T01:00:00Z']), 1000, 14400)
        self.assertEqual(evidence['observed_deadline'], 3600)
        self.assertEqual(evidence['source'], 'site_countdown')
        self.assertEqual(evidence['confirmed_at_epoch'], 1000)

    def test_no_machine_readable_clock_uses_a_labelled_conservative_fallback(self):
        for timestamps in ([], [None]):
            evidence = cooldown_evidence(self.observation(timestamps), 1000, 14400)
            self.assertEqual(evidence['observed_deadline'], 15400)
            self.assertEqual(evidence['source'], 'visible_panel_conservative_fallback')

    def test_invalid_naive_past_and_ambiguous_deadlines_are_never_guessed(self):
        for timestamps in (['bad'], [''], ['1970-01-01T01:00:00'],
                           ['1970-01-01T00:01:00Z'], ['1970-01-01T01:00:00Z'] * 2):
            with self.subTest(timestamps=timestamps), self.assertRaises(CooldownEvidenceError):
                cooldown_evidence(self.observation(timestamps), 1000, 14400)


class SiteCooldownVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.driver = sync_playwright().start()
        executable = os.environ.get('TEST_CHROMIUM_PATH') or shutil.which('chromium')
        try:
            cls.browser = cls.driver.chromium.launch(executable_path=executable, headless=True,
                                                   args=['--disable-dev-shm-usage'])
        except Exception:
            cls.driver.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.driver.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.set_default_timeout(500)
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'state.json'
        self.state = {'accounts': {'a': {'status': 'active', 'next_run': 0}}, 'global_pause': False}
        self.route = {'cooldown_seconds': 14400}
        self.settings = patch.dict(os.environ, {'COOLDOWN_SELECTOR': '#cooldown-label', 'LOGIN_FLOW': 'recorded'})
        self.settings.start()
        self.state_patch = patch('dragonbot.__main__.STATE', self.path)
        self.state_patch.start()
        self.clock = patch('dragonbot.__main__.time.time', return_value=1000)
        self.clock.start()
        self.page.set_content('''<section class="cooldown"><p id="cooldown-label">You can brave Threshing again in</p>
            <time data-countdown datetime="1970-01-01T01:00:00Z">00:43:20</time></section>''')

    def tearDown(self):
        self.clock.stop()
        self.state_patch.stop()
        self.settings.stop()
        self.context.close()
        self.directory.cleanup()

    def confirm(self, context='after_recorded_ending'):
        return confirm_account_cooldown(self.page, self.route, self.state, 'a', context)

    def test_observed_site_clock_and_confirmation_are_saved_before_logout(self):
        self.assertEqual(self.confirm(), 3600)
        record = load_state(self.path)['accounts']['a']
        self.assertEqual(record['next_run'], 3600)
        self.assertEqual(record['status'], 'logging_out')
        self.assertEqual(record['cooldown_confirmation']['confirmed_at_epoch'], 1000)
        self.assertEqual(record['cooldown_confirmation']['source'], 'site_countdown')

    def test_existing_later_deadline_is_never_shortened(self):
        self.state['accounts']['a']['next_run'] = 5000
        self.assertEqual(self.confirm(), 5000)
        evidence = self.state['accounts']['a']['cooldown_confirmation']
        self.assertEqual(evidence['observed_deadline'], 3600)
        self.assertEqual(evidence['scheduled_next_run'], 5000)

    def test_hidden_or_fading_panel_must_finish_revealing(self):
        self.page.evaluate('''() => {
            window.revealed=false;const panel=document.querySelector('.cooldown');panel.style.opacity='0';
            setTimeout(()=>{panel.style.opacity='1';window.revealed=true},100);
        }''')
        self.confirm()
        self.assertTrue(self.page.evaluate('window.revealed'))

    def test_no_visible_cooldown_panel_does_not_create_a_timer(self):
        self.page.set_default_timeout(150)
        self.page.locator('.cooldown').evaluate('element=>element.hidden=true')
        with self.assertRaises(BrowserTimeoutError):
            self.confirm()
        self.assertEqual(self.state['accounts']['a']['next_run'], 0)
        self.assertNotIn('cooldown_confirmation', self.state['accounts']['a'])
        self.assertFalse(self.path.exists())

    def test_bad_or_expired_site_clock_does_not_create_a_fallback_timer(self):
        for timestamp in ('bad', '1970-01-01T00:01:00Z'):
            self.page.locator('time').evaluate('(element,value)=>element.setAttribute("datetime",value)', timestamp)
            with self.assertRaises(CooldownEvidenceError):
                self.confirm()
            self.assertEqual(self.state['accounts']['a']['next_run'], 0)
            self.assertFalse(self.path.exists())

    def test_two_visible_cooldown_labels_cannot_confirm_an_ambiguous_panel(self):
        self.page.locator('body').evaluate('element=>element.insertAdjacentHTML("beforeend", "<p id=cooldown-label>Another cooldown</p>")')
        with self.assertRaises(CooldownEvidenceError):
            self.confirm()
        self.assertFalse(self.path.exists())

    def test_deadline_is_preserved_even_if_profile_logout_fails(self):
        with patch('dragonbot.__main__.logout_from_profile', side_effect=RuntimeError('fixture logout failure')):
            with self.assertRaises(RuntimeError):
                account_ready_for_story(self.page, self.route, self.state, 'a', 'cooldown')
        record = load_state(self.path)['accounts']['a']
        self.assertEqual(record['next_run'], 3600)
        self.assertIn('cooldown_confirmation', record)

    def test_site_is_checked_again_before_starting_another_attempt(self):
        self.state['accounts']['a']['next_run'] = 900
        self.page.locator('body').evaluate('element=>element.insertAdjacentHTML("beforeend", "<a href=/archway>Brave Threshing</a>")')
        with patch('dragonbot.__main__.logout_from_profile') as logout:
            self.assertFalse(account_ready_for_story(self.page, self.route, self.state, 'a', 'authenticated'))
            logout.assert_called_once()
        self.assertEqual(self.state['accounts']['a']['status'], 'cooldown')
        self.assertEqual(self.state['accounts']['a']['next_run'], 3600)
        self.assertEqual(self.page.url, 'about:blank')

    def test_existing_cooldown_does_not_claim_a_newly_observed_death(self):
        self.state['accounts']['a']['death_screen_seen'] = True
        self.confirm(context='already_cooling_on_login')
        self.assertFalse(self.state['accounts']['a']['cooldown_confirmation']['death_screen_seen'])

    def test_evidence_excludes_input_values_and_query_tokens(self):
        self.page.locator('.cooldown').evaluate('element=>element.insertAdjacentHTML("beforeend", "<input value=SECRET_CODE><a href=/rotunda?token=SECRET_QUERY>Rotunda</a>")')
        self.confirm()
        self.assertNotIn('SECRET', json.dumps(self.state['accounts']['a']['cooldown_confirmation']))


if __name__ == '__main__':
    unittest.main()

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from playwright.sync_api import sync_playwright
from dragonbot.__main__ import play

ROUTE = json.loads(Path('route.json').read_text())


class BrowserFlow(unittest.TestCase):
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

    def exercise(self, shadow=False, mail_failure=False, mismatch=False):
        rows = [[options[0], 'Other choice'] for options in ROUTE['steps']]
        if mismatch:
            rows[0] = ['Unrecognized choice']
        if shadow:
            rows[ROUTE['shadow_checkpoint']].append('Follow the shadow')
        html = '''<html><body><script>
        const rows = ROWS; let n = -2;
        function draw() {
          document.body.innerHTML = n === -2 ? '<input type="email"><button id="email" onclick="next()">Request</button>'
            : n === -1 ? '<input id="mfa"><button id="submit" onclick="next()">Verify</button>'
            : n >= rows.length ? '<h1 id="death">Dead</h1><button id="logout" onclick="n=-3;next()">Sign out</button>'
            : '<h1>Scene '+n+'</h1>'+rows[n].map(t=>'<button class="choice" onclick="next()">'+t+'</button>').join('');
        }
        function next() { n++; draw(); } draw();
        </script></body></html>'''.replace('ROWS', json.dumps(rows))
        context = self.browser.new_context()
        context.route('https://dragonkind.com/**', lambda route: route.fulfill(body=html, content_type='text/html'))
        wrapped_browser = MagicMock()
        wrapped_browser.new_context.return_value = context
        state = {'accounts': {}, 'global_pause': False}
        settings = {'EMAIL_SELECTOR': 'input[type=email]', 'EMAIL_SUBMIT_SELECTOR': '#email',
                    'MFA_SELECTOR': '#mfa', 'MFA_SUBMIT_SELECTOR': '#submit',
                    'CHOICES_SELECTOR': '.choice', 'DEATH_SELECTOR': '#death',
                    'SIGN_OUT_SELECTOR': '#logout', 'SHADOW_PAUSE_SCOPE': 'account'}
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with patch.dict(os.environ, settings), patch('dragonbot.__main__.inbox'), \
                     patch('dragonbot.__main__.highwater', return_value=1), \
                     patch('dragonbot.__main__.wait_code', return_value='123456'), \
                     patch('dragonbot.__main__.notify', side_effect=RuntimeError() if mail_failure else None) as mail:
                    play(wrapped_browser, ROUTE, state, ROUTE['accounts'][0])
                    persisted = json.loads(Path('data/state.json').read_text())
                    return persisted, mail.call_count
            finally:
                os.chdir(previous)

    def test_full_route_confirms_death_logout_and_cooldown(self):
        state, emails = self.exercise()
        record = state['accounts'][ROUTE['accounts'][0]]
        self.assertEqual(record['status'], 'cooldown')
        import time
        self.assertGreater(record['next_run'], time.time() + 14300)
        self.assertFalse(state['global_pause'])
        self.assertEqual(emails, 0)

    def test_shadow_pauses_only_one_account_and_notifies(self):
        state, emails = self.exercise(shadow=True)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'paused')
        self.assertFalse(state['global_pause'])
        self.assertEqual(emails, 1)

    def test_mail_failure_does_not_lose_pause(self):
        state, _ = self.exercise(shadow=True, mail_failure=True)
        self.assertTrue(state['accounts'][ROUTE['accounts'][0]]['notification_pending'])
        self.assertFalse(state['global_pause'])

    def test_unexpected_choice_pauses_everything(self):
        state, emails = self.exercise(mismatch=True)
        self.assertTrue(state['global_pause'])
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'paused')
        self.assertEqual(emails, 1)

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from playwright.sync_api import sync_playwright
from dragonbot.__main__ import play, prepare_login, complete_mfa, execute_choice

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

    def exercise(self, shadow=False, mail_failure=False, mismatch=False, auto_submit=False, forms=False):
        rows = [[options[0], 'Other choice'] for options in ROUTE['steps']]
        if mismatch:
            rows[0] = ['Unrecognized choice']
        if shadow:
            rows[ROUTE['shadow_checkpoint']].append('Follow the shadow')
        html = '''<html><body><script>
        const rows = ROWS; const forms = FORMS; let n = -2;
        function draw() {
          document.body.innerHTML = n === -2 ? '<input type="email"><button id="email" onclick="next()">Request</button>'
            : n === -1 ? '<input id="mfa"><button id="submit" onclick="next()">Verify</button>'
            : n >= rows.length ? '<h1 id="death">Dead</h1><button id="logout" onclick="n=-3;next()">Sign out</button>'
            : '<h1>Scene '+n+'</h1>'+(forms && n > 1 && rows[n][0].toLowerCase() !== 'continue'
              ? `<form id="choices" onsubmit="event.preventDefault();if(this.querySelector(':checked'))next()">`+
                rows[n].map((t,i)=>`<input hidden type="radio" name="choice" id="choice-${i}"><label class="choice" for="choice-${i}">${t}</label>`).join('')+
                '</form><button class="onward" data-choices-submit form="choices" type="submit">Next Scene</button>'
              : forms ? (n === 0
                  ? '<a class="button" href="/archway" onclick="event.preventDefault();next()">Brave Threshing</a>'
                  : n === 1 ? '<button class="button" type="submit" data-step="6" onclick="next()">Brave Threshing</button>'
                  : '<button class="onward" data-page-next data-script-only type="button" onclick="next()">Continue</button>')
                : rows[n].map(t=>'<button class="choice" onclick="next()">'+t+'</button>').join(''))+
                (forms ? '<button hidden data-page-next type="button">Continue</button>' : '');
        }
        function next() { n++; draw(); } draw();
        </script></body></html>'''.replace('ROWS', json.dumps(rows)).replace('FORMS', json.dumps(forms))
        if auto_submit:
            html = html.replace('<input id="mfa">', '<input id="mfa" oninput="if(this.value.length===6)next()">')
        context = self.browser.new_context()
        context.route('https://dragonkind.com/**', lambda route: route.fulfill(body=html, content_type='text/html'))
        wrapped_browser = MagicMock()
        wrapped_browser.new_context.return_value = context
        state = {'accounts': {}, 'global_pause': False}
        settings = {'EMAIL_SELECTOR': 'input[type=email]', 'EMAIL_SUBMIT_SELECTOR': '#email',
                    'MFA_SELECTOR': '#mfa', 'MFA_SUBMIT_SELECTOR': '' if auto_submit else '#submit',
                    'MFA_AUTO_SUBMIT': 'true' if auto_submit else 'false', 'AUTH_READY_SELECTOR': 'a.button[href="/archway"]' if forms else '.choice >> nth=0',
                    'CHOICES_SELECTOR': ('a.button[href="/archway"], button.button[type="submit"]:has-text("Brave Threshing"), button[data-page-next], label[for^="choice-"]' if forms else '.choice'), 'CHOICE_SUBMIT_SELECTOR': 'button[data-choices-submit][form=choices]', 'DEATH_SELECTOR': '#death',
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

    def test_welcome_reveals_real_text_email_field(self):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_content("""<button data-reveal-trigger data-step="4" onclick="document.getElementById('next-reader').hidden=false;this.hidden=true">Welcome, Candidate</button><input id="next-reader" type="text" hidden>""")
            with patch.dict(os.environ, {'EMAIL_SELECTOR': '#next-reader',
                                        'LOGIN_ENTRY_SELECTOR': 'button[data-reveal-trigger][data-step="4"]'}):
                prepare_login(page)
                page.locator('#next-reader').fill('one@gmail.com')
                self.assertEqual(page.locator('#next-reader').input_value(), 'one@gmail.com')
        finally:
            context.close()

    def test_visible_email_skips_welcome_button(self):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_content('<input id="next-reader" type="text">')
            with patch.dict(os.environ, {'EMAIL_SELECTOR': '#next-reader',
                                        'LOGIN_ENTRY_SELECTOR': 'button[data-reveal-trigger][data-step="4"]'}):
                prepare_login(page)
        finally:
            context.close()

    def test_full_route_after_automatic_mfa_submission(self):
        state, _ = self.exercise(auto_submit=True)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'cooldown')

    def test_auto_mfa_does_not_click_first_story_link(self):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_content("""<input id="next-code" oninput="if(this.value.length===7)document.body.innerHTML='&lt;a class=button href=/archway&gt;Brave Threshing&lt;/a&gt;'">""")
            with patch.dict(os.environ, {'MFA_SELECTOR': '#next-code', 'MFA_AUTO_SUBMIT': 'true',
                                        'AUTH_READY_SELECTOR': 'a.button[href="/archway"]',
                                        'MFA_SUBMIT_SELECTOR': ''}):
                complete_mfa(page, 'A1B-2C3')
                self.assertTrue(page.locator('a.button[href="/archway"]').is_visible())
                self.assertEqual(page.url, 'about:blank')
        finally:
            context.close()

    def test_full_route_selects_radios_and_submits_each_choice(self):
        state, emails = self.exercise(auto_submit=True, forms=True)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'cooldown')
        self.assertEqual(emails, 0)

    def test_shadow_on_radio_page_does_not_submit(self):
        state, emails = self.exercise(auto_submit=True, forms=True, shadow=True)
        record = state['accounts'][ROUTE['accounts'][0]]
        self.assertEqual(record['status'], 'paused')
        self.assertEqual(record['step'], ROUTE['shadow_checkpoint'])
        self.assertFalse(state['global_pause'])
        self.assertEqual(emails, 1)

    def test_unbound_label_is_not_submitted(self):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_content('<label for="choice-1">Enter the valley alone</label><button data-choices-submit form="choices">Next Scene</button>')
            with self.assertRaises(RuntimeError):
                execute_choice(page, page.locator('label'))
        finally:
            context.close()

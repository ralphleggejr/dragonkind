import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from playwright.sync_api import sync_playwright
from dragonbot.__main__ import play, prepare_login, complete_mfa, execute_choice, logout_after_death, finish_known_death_pages, configure_page, wait_story_ready, fill_login_email, submit_login_code_request, LoginRequestRejected, page_timeout_ms

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

    def exercise(self, shadow=False, mail_failure=False, mismatch=False, auto_submit=False, forms=False, logout_flow=False, shadow_position=2, red_dragon=False, result_flow=False):
        rows = [[options[0], 'Other choice'] for options in ROUTE['steps']]
        if red_dragon:
            rows[6] = ['Run away', 'Attack', 'Lower your eyes in respect']
        if forms:
            rows[ROUTE['shadow_checkpoint']][0] = 'Descend quickly in preparation for whatever’s coming your way'
        if mismatch:
            rows[0] = ['Unrecognized choice']
        if shadow:
            rows[ROUTE['shadow_checkpoint']].insert(shadow_position, 'Follow the shadow')
        if result_flow:
            rows = rows[:20]  # Supplied fatal jump ends the route before its fallback choices.
        html = '''<html><body><script>
        const rows = ROWS; const forms = FORMS; const logoutFlow = LOGOUT_FLOW; const resultFlow = RESULT_FLOW; let n = -2;
        function draw() {
          document.body.innerHTML = n === -2 ? '<input type="email"><button id="email" onclick="next()">Request</button>'
            : n === -1 ? '<input id="mfa"><button id="submit" onclick="next()">Verify</button>'
            : (logoutFlow || resultFlow) && n >= rows.length ? deathFlow()
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
        function deathFlow() {
          if(resultFlow) {
            if(n === rows.length) return `<section aria-label="Page 1 of 3" class="page" data-page="1"><p>Sometimes risks pay off…</p><strong class="display">Sometimes they don’t.</strong><button class="onward" data-page-next data-step="3" onclick="next()">Continue</button></section>`;
            if(n === rows.length+1) return `<p data-step="1">In your case, it’s the latter. Your boots slip on dragon scales, and you fall straight off what you hoped was your dragon, snapping your neck when you meet the ground.</p><button class="onward" data-page-next data-step="2" onclick="next()">Continue</button>`;
            if(n === rows.length+2) return `<p><span data-step="1">Reconsider your risk-to-reward ratios, read up on the Guide to Dragonkind, and come back in a few hours.</span><span data-beats="1" data-step="2">Your dragon still waits for you in the Threshing Valley.</span></p><a class="onward" data-step="3" href="/rotunda" onclick="event.preventDefault();next()">Continue</a><a class="icon-button" href="/rotunda">Rotunda</a>`;
            return `<p data-step="2" id="cooldown-label">You can brave Threshing again in</p><button class="icon-button" popovertarget="profile" type="button"><span>Profile</span></button><div id="profile" popover><button class="text-link" type="submit" onclick="n=-3;next()">Sign out</button></div>`;
          }
          if(n === rows.length) return '<h1 id="death">Dead</h1><button class="onward" data-page-next data-step="3" onclick="next()">Continue</button>';
          if(n === rows.length+1) return '<a class="icon-button" href="/rotunda" onclick="event.preventDefault();next()"><span>Rotunda</span></a>';
          return `<button class="icon-button" popovertarget="profile" type="button"><span>Profile</span></button><div id="profile" popover><button class="text-link" type="submit" onclick="n=-3;next()">Sign out</button></div>`;
        }
        function next() { n++; draw(); } draw();
        </script></body></html>'''.replace('ROWS', json.dumps(rows)).replace('FORMS', json.dumps(forms)).replace('LOGOUT_FLOW', json.dumps(logout_flow)).replace('RESULT_FLOW', json.dumps(result_flow))
        if auto_submit:
            html = html.replace('<input id="mfa">', '<input id="mfa" oninput="if(this.value.length===6)next()">')
        context = self.browser.new_context()
        context.route('https://dragonkind.com/**', lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
        wrapped_browser = MagicMock()
        wrapped_browser.new_context.return_value = context
        state = {'accounts': {}, 'global_pause': False}
        settings = {'EMAIL_SELECTOR': 'input[type=email]', 'EMAIL_SUBMIT_SELECTOR': '#email',
                    'MFA_SELECTOR': '#mfa', 'MFA_SUBMIT_SELECTOR': '' if auto_submit else '#submit',
                    'MFA_AUTO_SUBMIT': 'true' if auto_submit else 'false', 'AUTH_READY_SELECTOR': 'a.button[href="/archway"]' if forms else '.choice >> nth=0',
                    'CHOICES_SELECTOR': ('a.button[href="/archway"], button.button[type="submit"]:has-text("Brave Threshing"), button[data-page-next], label[for^="choice-"]' if forms else '.choice'), 'CHOICE_SUBMIT_SELECTOR': 'button[data-choices-submit][form=choices]', 'DEATH_SELECTOR': '#death',
                    'SIGN_OUT_SELECTOR': '#logout', 'SHADOW_PAUSE_SCOPE': 'account'}
        settings.update(LOGIN_EMAIL_CASE='original', DEATH_CONTINUE_SELECTOR='', ROTUNDA_SELECTOR='', PROFILE_SELECTOR='',
                        DEATH_NARRATIVE_SELECTOR='', COOLDOWN_SELECTOR='')
        if logout_flow:
            settings.update(DEATH_CONTINUE_SELECTOR='button.onward[data-page-next]',
                            ROTUNDA_SELECTOR='a.icon-button[href="/rotunda"]',
                            PROFILE_SELECTOR='button[popovertarget="profile"]',
                            SIGN_OUT_SELECTOR='button.text-link[type="submit"]:text-is("Sign out")')
        if result_flow:
            settings.update(
                DEATH_SELECTOR='span:text-is("Reconsider your risk-to-reward ratios, read up on the Guide to Dragonkind, and come back in a few hours.")',
                DEATH_NARRATIVE_SELECTOR='strong.display:text-is("Sometimes they don’t."), p:has-text("snapping your neck when you meet the ground")',
                DEATH_PAGE_NEXT_SELECTOR='button.onward[data-page-next]',
                DEATH_CONTINUE_SELECTOR='', ROTUNDA_SELECTOR='a.onward[href="/rotunda"]',
                COOLDOWN_SELECTOR='#cooldown-label', PROFILE_SELECTOR='button[popovertarget="profile"]',
                SIGN_OUT_SELECTOR='button.text-link[type="submit"]:text-is("Sign out")')
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

    def test_death_continue_rotunda_profile_and_signout(self):
        state, emails = self.exercise(auto_submit=True, forms=True, logout_flow=True)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'cooldown')
        self.assertEqual(emails, 0)

    def test_shadow_in_first_label_also_pauses(self):
        state, _ = self.exercise(auto_submit=True, forms=True, shadow=True, shadow_position=0)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'paused')
        self.assertFalse(state['global_pause'])

    def test_ordinary_continue_is_not_proof_of_death(self):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_content('<button data-page-next onclick="document.body.innerHTML=\"WRONG\"">Continue</button>')
            with patch.dict(os.environ, {'DEATH_SELECTOR': '#death'}), self.assertRaises(RuntimeError):
                logout_after_death(page)
            self.assertEqual(page.locator('body').inner_text(), 'Continue')
        finally:
            context.close()

    def test_red_dragon_branch_rejoins_forest_route(self):
        state, emails = self.exercise(auto_submit=True, forms=True, logout_flow=True, red_dragon=True)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'cooldown')
        self.assertEqual(emails, 0)

    def test_supplied_death_pages_confirm_result_then_site_cooldown_and_logout(self):
        state, emails = self.exercise(auto_submit=True, forms=True, result_flow=True)
        record = state['accounts'][ROUTE['accounts'][0]]
        self.assertEqual(record['status'], 'cooldown')
        self.assertGreater(record['next_run'], __import__('time').time() + 14300)
        self.assertEqual(emails, 0)

    def test_hidden_result_does_not_confirm_death(self):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_content('<p>Normal story page</p><span id="result" hidden>come back in a few hours</span><button data-page-next>Continue</button>')
            with patch.dict(os.environ, {'DEATH_SELECTOR': '#result', 'DEATH_NARRATIVE_SELECTOR': ''}):
                self.assertFalse(finish_known_death_pages(page))
        finally:
            context.close()

    def test_unrecognized_death_continuation_stops_before_any_further_choice(self):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_content("""<strong class="display">Sometimes they don’t.</strong><button data-page-next onclick="document.body.innerHTML='&lt;p&gt;Unexpected page&lt;/p&gt;'">Continue</button>""")
            with patch.dict(os.environ, {'DEATH_SELECTOR': '#result',
                                        'DEATH_NARRATIVE_SELECTOR': 'strong.display',
                                        'DEATH_PAGE_NEXT_SELECTOR': 'button[data-page-next]'}):
                with self.assertRaises(RuntimeError):
                    finish_known_death_pages(page)
                self.assertEqual(page.locator('body').inner_text(), 'Unexpected page')
        finally:
            context.close()

    def test_slow_intro_waits_for_delayed_controls_and_uses_configured_timeout(self):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_default_timeout(100)
            page.set_content("""<button id="welcome" hidden onclick="this.hidden=true;document.getElementById('email').hidden=false">Welcome, Candidate</button><input id="email" hidden><script>setTimeout(()=>document.getElementById('welcome').hidden=false, 700)</script>""")
            with patch.dict(os.environ, {'PAGE_LOAD_TIMEOUT_SECONDS': '2', 'EMAIL_SELECTOR': '#email',
                                        'LOGIN_ENTRY_SELECTOR': '#welcome'}):
                configure_page(page)
                prepare_login(page)
                self.assertTrue(page.locator('#email').is_visible())
                self.assertEqual(page.locator('#email').input_value(), '')
        finally:
            context.close()

    def test_story_readiness_waits_for_controls_after_background_loads(self):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_content("""<p>Loading scene artwork</p><button id="choice" hidden>Continue</button><script>setTimeout(()=>document.getElementById('choice').hidden=false, 200)</script>""")
            with patch.dict(os.environ, {'PAGE_LOAD_TIMEOUT_SECONDS': '2', 'CHOICES_SELECTOR': '#choice',
                                        'DEATH_SELECTOR': '#result', 'DEATH_NARRATIVE_SELECTOR': ''}):
                configure_page(page)
                wait_story_ready(page)
                self.assertTrue(page.locator('#choice').is_visible())
        finally:
            context.close()

    def test_email_committed_and_submit_waits_for_enabled_button_without_fixed_delay(self):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_default_timeout(2000)
            page.set_content("""<form><input id="email" name="reader" onchange="window.committed=this.value;setTimeout(()=>{window.readyAt=performance.now();document.querySelector('button').disabled=false},300)"><button type="button" disabled onclick="window.sent={email:new FormData(this.form).get('reader'),at:performance.now()}">Send me a code</button></form>""")
            with patch.dict(os.environ, {'EMAIL_SELECTOR': '#email', 'LOGIN_EMAIL_CASE': 'upper',
                                        'EMAIL_SUBMIT_SELECTOR': 'button', 'EMAIL_SUBMIT_DELAY_SECONDS': '9999'}), \
                 patch.object(page, 'wait_for_timeout', side_effect=AssertionError('Fixed browser sleep used')):
                self.assertEqual(fill_login_email(page, 'leggejr.co@gmail.com'), 'LEGGEJR.CO@GMAIL.COM')
                submit_login_code_request(page)
                sent = page.evaluate('window.sent')
                self.assertEqual(sent['email'], 'LEGGEJR.CO@GMAIL.COM')
                self.assertGreaterEqual(sent['at'], page.evaluate('window.readyAt'))
                self.assertEqual(page.evaluate('window.committed'), 'LEGGEJR.CO@GMAIL.COM')
        finally:
            context.close()

    def test_browser_timeout_defaults_to_five_minutes_and_cannot_exceed_cap(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(page_timeout_ms(), 300000)
        with patch.dict(os.environ, {'PAGE_LOAD_TIMEOUT_SECONDS': '900'}):
            self.assertEqual(page_timeout_ms(), 300000)
        with patch.dict(os.environ, {'PAGE_LOAD_TIMEOUT_SECONDS': '1'}):
            self.assertEqual(page_timeout_ms(), 1000)
        with patch.dict(os.environ, {'PAGE_LOAD_TIMEOUT_SECONDS': '0'}):
            with self.assertRaises(RuntimeError):
                page_timeout_ms()

    def test_native_login_rejection_is_reported_without_waiting_for_mfa(self):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            context.route('https://example.test/**', lambda route: route.fulfill(
                status=422 if route.request.method == 'POST' else 200,
                content_type='text/html; charset=utf-8',
                body='<h1>Say again?</h1>' if route.request.method == 'POST' else
                     '<form action="/enter" method="post"><input id="email" name="reader"><button id="send">Send me a code</button></form>'))
            page.goto('https://example.test/')
            with patch.dict(os.environ, {'EMAIL_SELECTOR': '#email', 'EMAIL_SUBMIT_SELECTOR': '#send',
                                        'LOGIN_EMAIL_CASE': 'upper'}):
                fill_login_email(page, 'leggejr.co@gmail.com')
                with self.assertRaisesRegex(LoginRequestRejected, 'HTTP 422'):
                    submit_login_code_request(page)
                self.assertEqual(page.locator('h1').inner_text(), 'Say again?')
        finally:
            context.close()

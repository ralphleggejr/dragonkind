import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeoutError
from dragonbot.__main__ import play, prepare_login, complete_mfa, execute_choice, logout_after_death, finish_known_death_pages, configure_page, wait_story_ready, fill_login_email, submit_login_code_request, LoginRequestRejected, page_timeout_ms, inspect_login_submission, wait_rendered, request_login_email, replay_login_page, mail_login_page, confirm_login, account_ready_for_story, login_page_status, story_page_status, click_transition, check_tree_checkpoint, TreeCheckpointReview

ROUTE = json.loads(Path('route.json').read_text())


class BrowserFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flow_patch = patch.dict(os.environ, {'LOGIN_FLOW': 'custom'})
        cls.flow_patch.start()
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
        cls.flow_patch.stop()

    def exercise(self, shadow=False, mail_failure=False, mismatch=False, auto_submit=False, forms=False, logout_flow=False, shadow_position=2, red_dragon=False, result_flow=False):
        rows = [[options[0]['prefix'] + ' the recorded continuation' if isinstance(options[0], dict)
                 else options[0], 'Other choice'] for options in ROUTE['steps']]
        if red_dragon:
            rows[6] = ['Run away', 'Attack', 'Lower your eyes in respect']
        if forms:
            rows[ROUTE['shadow_checkpoint']][0] = 'Climb down slowly to pursue the sound'
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
        settings.update(EMAIL_TYPING_DELAY_MS='0', LOGIN_EMAIL_CASE='original', DEATH_CONTINUE_SELECTOR='', ROTUNDA_SELECTOR='', PROFILE_SELECTOR='',
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
        self.assertEqual(emails, 1)

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

    def test_navigation_wait_handles_a_new_document_without_a_body(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        import time

        class Site(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, body, content_type='text/html'):
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.end_headers()
                self.wfile.write(body.encode())

            def do_GET(self):
                if self.path == '/slow.js':
                    # Simulate a parser-blocking asset; the new document has no body yet.
                    time.sleep(.3)
                    self.respond('window.parserReady=true;', 'application/javascript')
                elif self.path == '/archway':
                    self.respond('<html><head><script src="/slow.js"></script></head><body><h1>Your assignment, Cadet</h1><button>Brave Threshing</button></body></html>')
                elif self.path == '/play':
                    self.respond('<form method="post" action="/play"><button>Next Scene</button></form>')
                else:
                    self.respond('<a href="/archway">Brave Threshing</a>')

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                self.respond('<html><head><script src="/slow.js"></script></head><body><h1>Next scene</h1><button>Continue</button></body></html>')

        server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_default_timeout(3000)
            base = f'http://127.0.0.1:{server.server_port}'
            with self.subTest(navigation='Rotunda link to Archway'):
                page.goto(base + '/')
                click_transition(page, page.get_by_role('link', name='Brave Threshing'))
                self.assertTrue(page.get_by_role('button', name='Brave Threshing').is_visible())
                self.assertTrue(page.evaluate('window.parserReady'))
            with self.subTest(navigation='POST keeps the same /play URL'):
                page.goto(base + '/play')
                click_transition(page, page.get_by_role('button', name='Next Scene'))
                self.assertEqual(page.url, base + '/play')
                self.assertTrue(page.get_by_role('button', name='Continue').is_visible())
                self.assertTrue(page.evaluate('window.parserReady'))
        finally:
            context.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

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
        self.assertEqual(emails, 1)

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

    def test_resume_can_change_an_intentionally_dimmed_saved_radio_choice(self):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_default_timeout(1000)
            page.set_content('''<style>.choices:has(:checked) input:not(:checked)+label{opacity:.4}</style>
                <form id="choices" class="choices" onsubmit="event.preventDefault();document.body.innerHTML='<p>Next scene</p>'">
                <input hidden type="radio" name="choice" id="other" checked><label for="other">Another option</label>
                <input hidden type="radio" name="choice" id="wanted"><label for="wanted">Help your squadmate</label>
                </form><button form="choices" data-choices-submit>Next Scene</button>''')
            with patch.dict(os.environ, {'CHOICE_SUBMIT_SELECTOR': 'button[data-choices-submit]'}):
                self.assertEqual(page.locator('label[for=wanted]').evaluate('(e)=>getComputedStyle(e).opacity'), '0.4')
                execute_choice(page, page.locator('label[for=wanted]'))
                self.assertEqual(page.locator('body').inner_text(), 'Next scene')
        finally:
            context.close()

    def test_death_continue_rotunda_profile_and_signout(self):
        state, emails = self.exercise(auto_submit=True, forms=True, logout_flow=True)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'cooldown')
        self.assertEqual(emails, 1)

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
        self.assertEqual(emails, 1)

    def test_supplied_death_pages_confirm_result_then_site_cooldown_and_logout(self):
        state, emails = self.exercise(auto_submit=True, forms=True, result_flow=True)
        record = state['accounts'][ROUTE['accounts'][0]]
        self.assertEqual(record['status'], 'cooldown')
        self.assertGreater(record['next_run'], __import__('time').time() + 14300)
        self.assertEqual(emails, 1)

    def exercise_latest_recording(self, resumed=False, red=False, ending_pages=2,
                                  shadow_option=False, cooldown=True, direct_play=False, known_narrative=False,
                                  resume_scene=0, transparent_future=False, delayed_shadow=False, unknown_resume=False,
                                  role_only_continue=False, staggered_valley=False, hidden_shadow=False,
                                  never_reveal_valley=False, extra_option=None, hidden_extra=False,
                                  notification_bodies=None):
        account = ROUTE['accounts'][0]
        context = self.browser.new_context()
        requests, selected = [], []
        context.expose_function('captureChoice', lambda text: selected.append(text))
        rows = [alternatives[0]['prefix'] + ' the recorded continuation'
                if isinstance(alternatives[0], dict) else alternatives[0] for alternatives in ROUTE['steps'][2:]]
        rows[4] = 'Lower your eyes in respect' if red else 'Lower your eyes and turn to face it'
        rows[16] = 'Climb down slowly to pursue the sound'
        rows[19] = 'Lower your eyes but keep your head high'
        if unknown_resume:
            rows[resume_scene] = 'Unrecorded detour'
        entry = 'Continue your story' if resumed else 'Brave Threshing'
        play_html = '''<html><head><style>
            @keyframes recorded-step-in {0%{opacity:0;visibility:hidden;translate:0 6px}100%{opacity:1;visibility:visible;translate:0 0}}
            .late-option {animation:recorded-step-in .05s linear both}
            form#choices:not(:has(:checked)) + button[data-choices-submit] {display:none}
            </style></head><body><script>
            const rows=ROWS; let scene=START_SCENE; const endingPages=ENDING;
            function appendFuture() {
                if(TRANSPARENT_FUTURE) document.body.insertAdjacentHTML('beforeend', '<div style="opacity:0"><label for="choice-future-a">Help your squadmate</label><label for="choice-future-b">Investigate the scream</label></div>');
            }
            function draw() {
                if(scene >= rows.length) {
                    document.body.innerHTML = scene < rows.length+endingPages
                      ? `<p>Recorded ending page ${scene-rows.length+1}</p><button data-page-next onclick="next()">Continue</button>`
                      : '<p>End of attempt</p><a class="onward" href="/rotunda">Continue</a>';
                    if(KNOWN_NARRATIVE && scene===rows.length) document.body.insertAdjacentHTML('afterbegin', '<strong class="display">Sometimes they don’t.</strong>');
                    appendFuture();
                    return;
                }
                let label=rows[scene];
                if(label.toLowerCase()==='continue') {
                    document.body.innerHTML=`<p>Scene ${scene}</p><button data-page-next onclick="next()">Continue</button>`;
                } else {
                    let options=[label, 'Another choice'];
                    if(scene===4 && !RED_DRAGON) options=['Run and hope it didn’t see you', 'Lower your eyes and hold still', label];
                    if(scene===16 && (SHADOW || EXTRA_OPTION!==null)) options.push(EXTRA_OPTION || 'Follow the shadow');
                    if(STAGGERED_VALLEY && scene===2) options=['Enter the valley with two of your squadmates', 'Enter the valley with three of your squadmates', label];
                    document.body.innerHTML=`<p>Scene ${scene}</p><form id="choices" onsubmit="event.preventDefault();const input=this.querySelector(':checked');if(input){captureChoice(this.querySelector('label[for='+input.id+']').textContent);next()}">`+
                      options.map((text,i)=>`<div class="choice ${STAGGERED_VALLEY && scene===2 && i>0 ? 'late-option' : ''}" style="animation-delay:${i*.15}s"><input hidden type="radio" required name="choice" id="choice-${i}" value="${i}"><label for="choice-${i}">${text}</label></div>`).join('')+
                      '</form><button data-choices-submit form="choices">Next Scene</button>';
                    if(NEVER_REVEAL_VALLEY && scene===2) {
                        const option=document.querySelector('label[for="choice-2"]').parentElement;
                        option.classList.remove('late-option');option.style.visibility='hidden';
                    }
                    if(DELAYED_SHADOW && scene===16) {
                        const shadow=document.querySelector('label[for="choice-2"]');
                        shadow.style.opacity='0';setTimeout(()=>shadow.style.opacity='1', 150);
                    }
                    if(HIDDEN_SHADOW && scene===16) {
                        const shadow=document.querySelector('label[for="choice-2"]');
                        shadow.style.visibility='hidden';setTimeout(()=>shadow.style.visibility='visible', 150);
                    }
                    if(HIDDEN_EXTRA && scene===16) document.querySelector('label[for="choice-2"]').hidden=true;
                }
                appendFuture();
            }
            function next(){scene++;draw()} draw();
            </script></body></html>'''.replace('ROWS', json.dumps(rows)).replace('ENDING', str(ending_pages)).replace('EXTRA_OPTION', json.dumps(extra_option)).replace('HIDDEN_EXTRA', json.dumps(hidden_extra)).replace('DELAYED_SHADOW', json.dumps(delayed_shadow)).replace('HIDDEN_SHADOW', json.dumps(hidden_shadow)).replace('SHADOW', json.dumps(shadow_option)).replace('KNOWN_NARRATIVE', json.dumps(known_narrative)).replace('START_SCENE', str(resume_scene)).replace('TRANSPARENT_FUTURE', json.dumps(transparent_future)).replace('STAGGERED_VALLEY', json.dumps(staggered_valley)).replace('NEVER_REVEAL_VALLEY', json.dumps(never_reveal_valley)).replace('RED_DRAGON', json.dumps(red))
        if role_only_continue:
            play_html = play_html.replace('data-page-next', '')
        def site(route):
            from urllib.parse import urlsplit
            path = urlsplit(route.request.url).path
            requests.append(path)
            if path == '/enter':
                target = '/play' if direct_play else '/rotunda'
                body = f'<label for="code">Six-character code</label><input id="code" oninput="if(this.value.length===6)location.href=\'{target}\'">'
            elif path == '/rotunda':
                profile = '<button popovertarget="profile">Profile</button><div id="profile" popover><button class="text-link" onclick="location.href=\'/\'">Sign out</button></div>'
                if requests.count('/rotunda') > 1:
                    body = ('<p id="cooldown-label">You can brave Threshing again in</p>' if cooldown else '<p>No cooldown was confirmed</p>') + profile
                else:
                    body = f'<a class="button" href="/archway">{entry}</a>' + profile
            elif path == '/archway':
                body = f'<button type="button" onclick="location.href=\'/play\'">{entry}</button>'
            elif path == '/play':
                body = play_html if not direct_play or '/archway' in requests else '<form id="choices"><input id="choice-0" type="radio"><label for="choice-0">A mid-story choice</label></form>'
            else:
                body = '<form method="post" action="/enter"><label for="email">Email Address</label><input id="email" name="reader"><button aria-label="Send me a code">→</button></form>'
            route.fulfill(content_type='text/html; charset=utf-8', body=body)
        context.route('https://dragonkind.com/**', site)
        wrapped = MagicMock()
        wrapped.new_context.return_value = context
        state = {'accounts': {}, 'global_pause': False}
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'LOGIN_FLOW': 'recorded', 'LOGIN_EMAIL_CASE': 'original',
                    'PAGE_LOAD_TIMEOUT_SECONDS': '1', 'MFA_AUTO_SUBMIT': 'true',
                    'CHOICES_SELECTOR': 'a.button[href="/archway"], button[data-page-next], label[for^="choice-"]',
                    'CHOICE_SUBMIT_SELECTOR': 'button[data-choices-submit][form="choices"]',
                    'DEATH_SELECTOR': '#terminal-death', 'DEATH_NARRATIVE_SELECTOR': 'strong.display' if known_narrative else '',
                    'COOLDOWN_SELECTOR': '#cooldown-label', 'PROFILE_SELECTOR': 'button[popovertarget="profile"]',
                    'SIGN_OUT_SELECTOR': 'button.text-link', 'SHADOW_PAUSE_SCOPE': 'account'}), \
                 patch('dragonbot.__main__.STATE', Path(directory) / 'state.json'), \
                 patch('dragonbot.__main__.inbox'), patch('dragonbot.__main__.highwater', return_value=10), \
                 patch('dragonbot.__main__.wait_code', return_value='ABC123'), \
                 patch('dragonbot.__main__.notify') as notify, patch('playwright.sync_api.Page.screenshot'):
                play(wrapped, ROUTE, state, account)
                saved = json.loads((Path(directory) / 'state.json').read_text())
                if notification_bodies is not None:
                    notification_bodies.extend(call.args[1] for call in notify.call_args_list)
        return saved, requests, selected, notify.call_count

    def test_new_brown_walkthrough_reaches_cooldown_and_signs_out(self):
        notifications = []
        state, requests, selected, emails = self.exercise_latest_recording(notification_bodies=notifications)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'cooldown')
        self.assertFalse(state['global_pause'])
        self.assertIn('Lower your eyes and turn to face it', selected)
        self.assertNotIn('Lower your eyes and hold still', selected)
        self.assertIn('Climb down slowly to pursue the sound', selected)
        self.assertIn('Lower your eyes but keep your head high', selected)
        self.assertEqual(requests.count('/rotunda'), 2)
        self.assertEqual(requests[-1], '/')
        self.assertEqual(emails, 1)
        self.assertIn('Outcome: CONTINUED', notifications[0])
        self.assertIn('Safety checks recorded: 3', notifications[0])
        for stage in ('initial', 'before selection', 'before submission'):
            self.assertIn(stage, notifications[0])
        self.assertIn('Options found: 2', notifications[0])
        self.assertIn('Shadow found: NO', notifications[0])
        self.assertIn('Climb down slowly to pursue the sound', notifications[0])
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['last_tree_report']['status'], 'sent')

    def test_continue_your_story_recording_accepts_three_ending_pages(self):
        state, requests, selected, _ = self.exercise_latest_recording(resumed=True, ending_pages=3)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'cooldown')
        self.assertIn('/archway', requests)
        self.assertEqual(selected[-1], 'Lower your eyes but keep your head high')

    def test_new_red_walkthrough_uses_same_recorded_post_tree_path(self):
        state, _, selected, _ = self.exercise_latest_recording(red=True)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'cooldown')
        self.assertIn('Lower your eyes in respect', selected)
        self.assertNotIn('Lower your eyes and turn to face it', selected)

    def test_direct_mid_story_login_returns_to_rotunda_entry_before_choices(self):
        state, requests, selected, _ = self.exercise_latest_recording(resumed=True, direct_play=True)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'cooldown')
        self.assertLess(requests.index('/play'), requests.index('/rotunda'))
        self.assertNotIn('A mid-story choice', selected)

    def test_shadow_still_pauses_before_new_tree_choice(self):
        state, requests, selected, emails = self.exercise_latest_recording(shadow_option=True)
        record = state['accounts'][ROUTE['accounts'][0]]
        self.assertEqual(record['status'], 'paused')
        self.assertEqual(record['step'], ROUTE['shadow_checkpoint'])
        self.assertFalse(state['global_pause'])
        self.assertNotIn('Climb down slowly to pursue the sound', selected)
        self.assertEqual(requests.count('/rotunda'), 1)
        self.assertEqual(emails, 1)

    def test_tree_third_option_without_shadow_pauses_and_emails_all_labels(self):
        notifications = []
        state, requests, selected, emails = self.exercise_latest_recording(
            extra_option='Follow an unfamiliar movement', notification_bodies=notifications)
        record = state['accounts'][ROUTE['accounts'][0]]
        self.assertEqual(record['status'], 'paused')
        self.assertEqual(record['step'], ROUTE['shadow_checkpoint'])
        self.assertEqual(record['pause_scope'], 'account')
        self.assertFalse(state['global_pause'])
        self.assertNotIn('Climb down slowly to pursue the sound', selected)
        self.assertEqual(requests.count('/rotunda'), 1)
        self.assertEqual(emails, 1)
        check = record['tree_checkpoint_checks'][-1]
        self.assertEqual(check['option_count'], 3)
        self.assertFalse(check['shadow_found'])
        self.assertIn('Options found: 3', notifications[0])
        for option in check['options']:
            self.assertIn(option['text'], notifications[0])

    def test_resumed_tree_counts_a_never_visible_third_option(self):
        state, _, selected, emails = self.exercise_latest_recording(
            resumed=True, resume_scene=16, extra_option='Follow the mysterious shape', hidden_extra=True)
        record = state['accounts'][ROUTE['accounts'][0]]
        self.assertEqual(record['status'], 'paused')
        self.assertEqual(record['pause_scope'], 'account')
        self.assertEqual(record['tree_checkpoint_checks'][-1]['option_count'], 3)
        self.assertFalse(record['tree_checkpoint_checks'][-1]['options'][-1]['painted'])
        self.assertEqual(selected, [])
        self.assertEqual(emails, 1)

    def exercise_tree_safety_checks(self, extra_html='', after_initial='', on_select='', animated=False,
                                    selector='label[for^="choice-"]', other_text='Another choice', setup_script=''):
        context = self.browser.new_context()
        state = {'accounts': {'one@gmail.com': {'step': ROUTE['shadow_checkpoint'], 'next_run': 123}},
                 'global_pause': False}
        try:
            page = context.new_page()
            page.set_default_timeout(1500)
            page.set_content('''<style>@keyframes reveal {from {opacity:0} to {opacity:1}}
                .revealing {animation:reveal .2s both}</style>
                <script>window.selections=0;window.submissions=0;</script>
                <form id="choices" onsubmit="event.preventDefault();window.submissions++;document.body.innerHTML='Next scene'">
                <input hidden type="radio" name="choice" id="choice-0">
                <label for="choice-0">Climb down slowly to pursue the sound</label>
                <input hidden type="radio" name="choice" id="choice-1">
                <label for="choice-1">OTHER_TEXT</label>EXTRA_HTML</form>
                <button form="choices" data-choices-submit>Next Scene</button>'''
                .replace('OTHER_TEXT', other_text).replace('EXTRA_HTML', extra_html))
            page.locator('#choice-0').evaluate("""(input, onSelect) => input.addEventListener('change', () => {
                window.selections++; if(onSelect) Function(onSelect)();
            })""", on_select)
            if animated:
                page.locator('label[for="choice-1"]').evaluate("element => element.classList.add('revealing')")
            if setup_script:
                page.evaluate(setup_script)
            with tempfile.TemporaryDirectory() as directory:
                with patch.dict(os.environ, {'LOGIN_FLOW': 'recorded', 'CHOICES_SELECTOR': selector,
                                            'CHOICE_SUBMIT_SELECTOR': 'button[data-choices-submit]'}), \
                     patch('dragonbot.__main__.STATE', Path(directory) / 'state.json'), \
                     patch('builtins.print') as output:
                    error = None
                    try:
                        _, expected = check_tree_checkpoint(page, state, 'one@gmail.com', 'initial')
                        if after_initial:
                            page.evaluate(after_initial)
                        execute_choice(page, page.locator('label[for="choice-0"]'),
                            before_action=lambda stage: check_tree_checkpoint(
                                page, state, 'one@gmail.com', stage, expected=expected))
                    except TreeCheckpointReview as failure:
                        error = str(failure)
                    saved = json.loads((Path(directory) / 'state.json').read_text())
                    log = '\n'.join(str(call.args[0]) for call in output.call_args_list)
                    return saved, error, page.evaluate('window.selections'), page.evaluate('window.submissions'), log
        finally:
            context.close()

    def test_tree_two_options_are_logged_and_checked_three_times(self):
        state, error, selections, submissions, log = self.exercise_tree_safety_checks()
        self.assertIsNone(error)
        self.assertEqual((selections, submissions), (1, 1))
        checks = state['accounts']['one@gmail.com']['tree_checkpoint_checks']
        self.assertEqual([check['stage'] for check in checks],
                         ['initial', 'before selection', 'before submission'])
        self.assertTrue(all(check['option_count'] == 2 and not check['shadow_found'] for check in checks))
        self.assertIn('Climb down slowly to pursue the sound', log)
        self.assertIn('Another choice', log)
        self.assertEqual(state['accounts']['one@gmail.com']['next_run'], 123)

    def test_tree_shadow_is_checked_even_with_only_two_options(self):
        state, error, selections, submissions, _ = self.exercise_tree_safety_checks(other_text='Follow a SHADOW')
        self.assertIn('shadow', error)
        self.assertEqual((selections, submissions), (0, 0))
        self.assertEqual(state['accounts']['one@gmail.com']['tree_checkpoint_checks'][-1]['option_count'], 2)

    def test_tree_counts_extra_options_excluded_by_configured_selector(self):
        state, error, selections, submissions, _ = self.exercise_tree_safety_checks(
            extra_html='<input hidden type="radio" name="choice" id="choice-2"><label hidden for="choice-2">A new route</label>',
            selector='label[for="choice-0"], label[for="choice-1"]')
        self.assertIn('3 options', error)
        self.assertEqual((selections, submissions), (0, 0))
        self.assertEqual(state['accounts']['one@gmail.com']['tree_checkpoint_checks'][-1]['option_count'], 3)

    def test_tree_second_check_catches_a_new_third_option_before_selection(self):
        mutation = "document.querySelector('#choices').insertAdjacentHTML('beforeend', '<input type=radio name=choice id=choice-2><label for=choice-2>Follow the shadow</label>')"
        state, error, selections, submissions, _ = self.exercise_tree_safety_checks(after_initial=mutation)
        self.assertIn('shadow', error)
        self.assertEqual((selections, submissions), (0, 0))
        check = state['accounts']['one@gmail.com']['tree_checkpoint_checks'][-1]
        self.assertEqual(check['stage'], 'before selection')
        self.assertEqual(check['option_count'], 3)

    def test_tree_checks_again_after_selection_before_next_scene(self):
        mutation = "document.querySelector('#choices').insertAdjacentHTML('beforeend', '<input type=radio name=choice id=choice-2><label for=choice-2>Follow an unfamiliar shape</label>')"
        state, error, selections, submissions, _ = self.exercise_tree_safety_checks(on_select=mutation)
        self.assertIn('3 options', error)
        self.assertEqual((selections, submissions), (1, 0))
        check = state['accounts']['one@gmail.com']['tree_checkpoint_checks'][-1]
        self.assertEqual(check['stage'], 'before submission')
        self.assertEqual(check['option_count'], 3)

    def test_tree_waits_for_finite_reveal_animations_before_choosing(self):
        state, error, selections, submissions, log = self.exercise_tree_safety_checks(animated=True)
        self.assertIsNone(error)
        self.assertEqual((selections, submissions), (1, 1))
        self.assertIn('initial after reveal', [check['stage'] for check in state['accounts']['one@gmail.com']['tree_checkpoint_checks']])
        self.assertIn('waiting for all options', log)

    def test_tree_detects_a_third_option_inserted_during_reveal_animation(self):
        mutation = "setTimeout(() => document.querySelector('#choices').insertAdjacentHTML('beforeend', '<input hidden type=radio name=choice id=choice-2><label hidden for=choice-2>Follow the shadow</label>'), 100)"
        state, error, selections, submissions, _ = self.exercise_tree_safety_checks(
            animated=True, setup_script=mutation)
        self.assertIn('shadow', error)
        self.assertEqual((selections, submissions), (0, 0))
        self.assertEqual(state['accounts']['one@gmail.com']['tree_checkpoint_checks'][-1]['option_count'], 3)

    def test_tree_stops_on_changed_wording_between_checks(self):
        state, error, selections, submissions, _ = self.exercise_tree_safety_checks(
            after_initial="document.querySelector('label[for=choice-1]').textContent='An unexpected replacement'")
        self.assertIn('changed between safety checks', error)
        self.assertEqual((selections, submissions), (0, 0))

    def test_tree_unlabelled_radio_is_counted(self):
        _, error, selections, submissions, _ = self.exercise_tree_safety_checks(
            extra_html='<input hidden type="radio" name="choice" id="choice-2">')
        self.assertIn('3 options', error)
        self.assertEqual((selections, submissions), (0, 0))

    def test_recorded_ending_never_sets_cooldown_without_site_confirmation(self):
        state, _, _, _ = self.exercise_latest_recording(cooldown=False)
        record = state['accounts'][ROUTE['accounts'][0]]
        self.assertEqual(record['status'], 'paused')
        self.assertEqual(record.get('next_run', 0), 0)
        self.assertTrue(state['global_pause'])

    def test_recorded_ending_stops_after_three_continue_buttons(self):
        state, requests, _, _ = self.exercise_latest_recording(ending_pages=4)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'paused')
        self.assertEqual(requests.count('/rotunda'), 1)
        self.assertTrue(state['global_pause'])

    def test_recorded_ending_accepts_changing_death_prose_and_requires_cooldown(self):
        state, _, _, _ = self.exercise_latest_recording(known_narrative=True)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'cooldown')

    def test_resumed_brown_story_selects_face_it_when_hold_still_is_also_present(self):
        state, _, selected, _ = self.exercise_latest_recording(resumed=True, resume_scene=4)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'cooldown')
        self.assertEqual(selected[0], 'Lower your eyes and turn to face it')
        self.assertNotIn('Lower your eyes and hold still', selected)
        self.assertNotIn('Enter the valley alone', selected)

    def test_resumed_continue_page_reveals_and_identifies_the_next_known_choice(self):
        state, _, selected, _ = self.exercise_latest_recording(resumed=True, resume_scene=10)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'cooldown')
        self.assertEqual(selected[0], 'Allow your squadmate to exact their own revenge')

    def test_resumed_tree_checkpoint_still_pauses_on_shadow(self):
        state, _, selected, emails = self.exercise_latest_recording(resumed=True, resume_scene=16, shadow_option=True)
        record = state['accounts'][ROUTE['accounts'][0]]
        self.assertEqual(record['status'], 'paused')
        self.assertEqual(record['step'], ROUTE['shadow_checkpoint'])
        self.assertFalse(state['global_pause'])
        self.assertEqual(selected, [])
        self.assertEqual(emails, 1)

    def test_resumed_result_link_requires_site_cooldown_and_no_extra_choices(self):
        state, _, selected, _ = self.exercise_latest_recording(resumed=True, resume_scene=22)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'cooldown')
        self.assertEqual(selected, [])

    def test_resume_ignores_transparent_future_options_during_continue_pages(self):
        state, _, _, _ = self.exercise_latest_recording(resumed=True, transparent_future=True)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'cooldown')
        self.assertFalse(state['global_pause'])

    def test_resume_accepts_recorded_continue_role_without_old_css_attributes(self):
        state, _, _, _ = self.exercise_latest_recording(resumed=True, role_only_continue=True)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'cooldown')

    def test_resume_waits_for_delayed_shadow_option_before_choosing(self):
        state, _, selected, _ = self.exercise_latest_recording(resumed=True, resume_scene=16,
                                                             shadow_option=True, delayed_shadow=True)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'paused')
        self.assertFalse(state['global_pause'])
        self.assertEqual(selected, [])

    def test_resumed_valley_waits_for_solo_option_after_squadmate_options_reveal(self):
        state, _, selected, _ = self.exercise_latest_recording(resumed=True, staggered_valley=True)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'cooldown')
        self.assertEqual(selected[0], 'Enter the valley alone')
        self.assertNotIn('Enter the valley with two of your squadmates', selected)

    def test_fresh_valley_waits_for_the_same_staggered_choice_reveal(self):
        state, _, selected, _ = self.exercise_latest_recording(staggered_valley=True)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'cooldown')
        self.assertEqual(selected[0], 'Enter the valley alone')

    def test_wait_for_visibility_hidden_shadow_before_tree_selection(self):
        state, _, selected, _ = self.exercise_latest_recording(resumed=True, resume_scene=16,
                                                             shadow_option=True, hidden_shadow=True)
        self.assertEqual(state['accounts'][ROUTE['accounts'][0]]['status'], 'paused')
        self.assertFalse(state['global_pause'])
        self.assertEqual(selected, [])

    def test_missing_solo_option_times_out_without_taking_squadmate_route(self):
        state, _, selected, _ = self.exercise_latest_recording(resumed=True, staggered_valley=True,
                                                             never_reveal_valley=True)
        record = state['accounts'][ROUTE['accounts'][0]]
        self.assertEqual(record['status'], 'paused')
        self.assertIn('TimeoutError', record['reason'])
        self.assertEqual(selected, [])
        options = {row['text']: row for row in record['story_diagnostic']['controls']}
        self.assertEqual(options['Enter the valley alone']['visibility'], 'hidden')

    def test_unknown_resumed_choice_saves_actual_options_and_controlled_reason(self):
        state, _, selected, _ = self.exercise_latest_recording(resumed=True, resume_scene=4, unknown_resume=True)
        record = state['accounts'][ROUTE['accounts'][0]]
        self.assertEqual(record['status'], 'paused')
        self.assertIn('Cannot uniquely identify the current story step', record['reason'])
        self.assertIn('Unrecorded detour', [row['text'] for row in record['story_diagnostic']['controls']])
        self.assertEqual(selected, [])

    def test_story_diagnostic_excludes_input_values_and_link_tokens(self):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_content('<input value="SECRET_CODE"><input type="hidden" value="SECRET_TOKEN">'
                             '<a href="/rotunda?token=SECRET_QUERY">Rotunda</a>'
                             '<label for="choice-0">A visible option</label>'
                             '<label style="opacity:0" for="choice-1">A future option</label>')
            diagnostic = story_page_status(page)
            self.assertNotIn('SECRET', json.dumps(diagnostic))
            rows = {row['text']: row for row in diagnostic['controls']}
            self.assertTrue(rows['A visible option']['painted'])
            self.assertFalse(rows['A future option']['painted'])
        finally:
            context.close()

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
                                        'EMAIL_SUBMIT_SELECTOR': 'button', 'EMAIL_SUBMIT_DELAY_SECONDS': '9999', 'EMAIL_TYPING_DELAY_MS': '0'}), \
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
                                        'LOGIN_EMAIL_CASE': 'upper', 'EMAIL_TYPING_DELAY_MS': '0'}):
                fill_login_email(page, 'leggejr.co@gmail.com')
                with self.assertRaisesRegex(LoginRequestRejected, 'HTTP 422'):
                    submit_login_code_request(page)
                self.assertEqual(page.locator('h1').inner_text(), 'Say again?')
        finally:
            context.close()

    def test_email_keyboard_events_finish_before_arrow_is_clicked_once(self):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_content("""<form><input id="email" name="reader" onkeydown="window.keys=(window.keys||0)+1"><button aria-label="Send me a code" type="button" onclick="window.clicks=(window.clicks||0)+1;window.submitted=new FormData(this.form).get('reader');window.keyCountAtClick=window.keys">→</button></form>""")
            with patch.dict(os.environ, {'EMAIL_SELECTOR': '#email', 'LOGIN_EMAIL_CASE': 'upper',
                                        'EMAIL_SUBMIT_SELECTOR': 'button[aria-label="Send me a code"]',
                                        'EMAIL_TYPING_DELAY_MS': '5'}):
                fill_login_email(page, 'leggejr.co@gmail.com')
                self.assertIsNone(page.evaluate('window.clicks'))
                submit_login_code_request(page)
                self.assertEqual(page.evaluate('window.clicks'), 1)
                self.assertEqual(page.evaluate('window.submitted'), 'LEGGEJR.CO@GMAIL.COM')
                self.assertGreaterEqual(page.evaluate('window.keyCountAtClick'), len('LEGGEJR.CO@GMAIL.COM'))
        finally:
            context.close()

    def test_login_dry_run_captures_exact_payload_without_reaching_submission_endpoint(self):
        context = self.browser.new_context()
        reached_posts = []
        def server(route):
            if route.request.method == 'POST':
                reached_posts.append(route.request.url)
            route.fulfill(content_type='text/html; charset=utf-8', body=
                '<form action="/enter" method="post"><input type="hidden" name="_csrf_token" value="SECRET"><input id="email" name="reader"><button id="send">Send me a code</button></form>')
        context.route('https://dragonkind.com/**', server)
        try:
            page = context.new_page()
            page.set_default_timeout(2000)
            with patch.dict(os.environ, {'EMAIL_SELECTOR': '#email', 'LOGIN_ENTRY_SELECTOR': '',
                                        'EMAIL_SUBMIT_SELECTOR': '#send', 'EMAIL_TYPING_DELAY_MS': '0',
                                        'LOGIN_EMAIL_CASE': 'original'}):
                summary = inspect_login_submission(page, 'leggejr.co@gmail.com')
            self.assertTrue(summary['reader_matches_account_exactly'])
            self.assertTrue(summary['csrf_field_nonempty'])
            self.assertEqual(reached_posts, [])
            self.assertNotIn('SECRET', json.dumps(summary))
        finally:
            context.close()

    def test_login_waits_for_transparent_control_and_ancestor_to_be_painted(self):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_default_timeout(2000)
            page.set_content("""<div id="panel" style="opacity:0"><input id="email" oninput="window.typedAt=performance.now()"></div><script>setTimeout(()=>{document.querySelector('#panel').style.opacity='1';window.revealedAt=performance.now()},200)</script>""")
            with patch.dict(os.environ, {'EMAIL_SELECTOR': '#email', 'EMAIL_TYPING_DELAY_MS': '0'}):
                fill_login_email(page, 'leggejr.co@gmail.com')
            self.assertGreaterEqual(page.evaluate('window.typedAt'), page.evaluate('window.revealedAt'))
        finally:
            context.close()

    def exercise_login_retry(self, statuses, replace_page=False):
        context = self.browser.new_context()
        roots, posts, csrf_values = [], [], []
        def server(route):
            path = __import__('urllib.parse', fromlist=['urlsplit']).urlsplit(route.request.url).path
            if route.request.method == 'POST':
                posts.append(route.request.url)
                fields = __import__('urllib.parse', fromlist=['parse_qs']).parse_qs(route.request.post_data)
                csrf_values.append(fields['_csrf_token'][0])
                status = statuses[min(len(posts)-1, len(statuses)-1)]
                route.fulfill(status=status, content_type='text/html', body='<h1>Rejected</h1>' if status >= 400 else '<input id="code">')
            else:
                roots.append(path)
                route.fulfill(content_type='text/html', body=f'<form action="/enter" method="post"><input type="hidden" name="_csrf_token" value="token-{len(roots)}"><input id="email" name="reader"><button id="send">Send me a code</button></form>')
        context.route('https://dragonkind.com/**', server)
        try:
            page = context.new_page()
            page.set_default_timeout(2000)
            page.goto('https://dragonkind.com')
            callback = MagicMock(side_effect=[10, 20])
            failure = None
            boundary = None
            with patch.dict(os.environ, {'EMAIL_SELECTOR': '#email', 'LOGIN_ENTRY_SELECTOR': '',
                                        'EMAIL_SUBMIT_SELECTOR': '#send', 'EMAIL_TYPING_DELAY_MS': '0',
                                        'LOGIN_EMAIL_CASE': 'original'}):
                try:
                    def new_page_for_retry():
                        page.close()
                        replacement = context.new_page()
                        replacement.set_default_timeout(2000)
                        replacement.goto('https://dragonkind.com')
                        prepare_login(replacement)
                        return replacement
                    boundary = request_login_email(page, 'leggejr.co@gmail.com', callback,
                                                   restart_page=new_page_for_retry if replace_page else None)
                except LoginRequestRejected as error:
                    failure = error.status
            return roots, posts, csrf_values, boundary, failure, callback.call_count
        finally:
            context.close()

    def test_rejected_login_reloads_fresh_csrf_and_retries_once(self):
        roots, posts, csrf, boundary, failure, calls = self.exercise_login_retry([422, 200])
        self.assertEqual(len(posts), 2)
        self.assertEqual(len(roots), 2)
        self.assertEqual(csrf, ['token-1', 'token-2'])
        self.assertEqual(boundary, 20)
        self.assertIsNone(failure)
        self.assertEqual(calls, 2)

    def test_repeated_rejection_stops_after_second_submission(self):
        roots, posts, _, _, failure, _ = self.exercise_login_retry([422])
        self.assertEqual(len(posts), 2)
        self.assertEqual(len(roots), 2)
        self.assertEqual(failure, 422)

    def test_rate_limit_is_not_retried(self):
        roots, posts, _, _, failure, _ = self.exercise_login_retry([429])
        self.assertEqual(len(posts), 1)
        self.assertEqual(len(roots), 1)
        self.assertEqual(failure, 429)

    def test_login_retry_continues_on_replacement_page(self):
        roots, posts, csrf, boundary, failure, calls = self.exercise_login_retry([422, 200], replace_page=True)
        self.assertEqual(len(posts), 2)
        self.assertEqual(len(roots), 2)
        self.assertEqual(csrf, ['token-1', 'token-2'])
        self.assertEqual(boundary, 20)
        self.assertIsNone(failure)
        self.assertEqual(calls, 2)

    def test_recorded_role_login_uses_click_fill_then_arrow_and_confirms_mfa(self):
        context = self.browser.new_context()
        submitted = []
        def site(route):
            request = route.request
            path = __import__('urllib.parse', fromlist=['urlsplit']).urlsplit(request.url).path
            if request.method == 'POST':
                submitted.append(__import__('urllib.parse', fromlist=['parse_qs']).parse_qs(request.post_data))
                route.fulfill(content_type='text/html', body="""<label for="code">Six-character code</label><input id="code" oninput="if(this.value.length===6)location.href='/rotunda'">""")
            elif path == '/code':
                route.fulfill(content_type='text/html', body="""<label for="code">Six-character code</label><input id="code" oninput="if(this.value.length===6)location.href='/rotunda'">""")
            elif path == '/rotunda':
                route.fulfill(content_type='text/html', body='<a href="/archway" onclick="event.preventDefault();document.body.dataset.clicked=1">Brave Threshing</a>')
            else:
                route.fulfill(content_type='text/html', body="""<button onclick="this.hidden=true;document.querySelector('form').hidden=false">Welcome, Candidate</button><form hidden action="/enter" method="post"><label for="reader">Email Address</label><input id="reader" name="reader" onkeydown="let h=document.createElement('input');h.type='hidden';h.name='keyboard_event';h.value='1';this.form.appendChild(h)"><button aria-label="Send me a code">→</button></form>""")
        context.route('https://dragonkind.com/**', site)
        try:
            page = context.new_page()
            page.set_default_timeout(2000)
            with patch.dict(os.environ, {'LOGIN_FLOW': 'recorded', 'LOGIN_EMAIL_CASE': 'original', 'MFA_AUTO_SUBMIT': 'true'}):
                replay_login_page(page, 'ralphleggejr@gmail.com')
                self.assertEqual(submitted, [{'reader': ['ralphleggejr@gmail.com']}])
                self.assertEqual(page.get_by_role('textbox', name='Six-character code').input_value(), '')
                complete_mfa(page, '123456')
                self.assertEqual(page.url, 'https://dragonkind.com/rotunda')
                self.assertTrue(page.get_by_role('link', name='Brave Threshing').is_visible())
                self.assertIsNone(page.locator('body').get_attribute('data-clicked'))
        finally:
            context.close()

    def test_recorded_login_does_not_skip_failed_mfa_by_navigating_to_rotunda(self):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_default_timeout(500)
            page.set_content('<label for="code">Six-character code</label><input id="code"><a href="/archway" hidden>Brave Threshing</a>')
            requests = []
            page.on('request', lambda request: requests.append(request.url))
            with patch.dict(os.environ, {'LOGIN_FLOW': 'recorded', 'MFA_AUTO_SUBMIT': 'true'}):
                with self.assertRaises(BrowserTimeoutError):
                    complete_mfa(page, '123456')
            self.assertEqual(page.url, 'about:blank')
            self.assertEqual(requests, [])
        finally:
            context.close()

    def test_recorded_login_requires_authenticated_rotunda_content_not_just_url(self):
        context = self.browser.new_context()
        try:
            context.route('https://dragonkind.com/rotunda', lambda route: route.fulfill(
                content_type='text/html', body='<h1>Please sign in</h1><button>Welcome, Candidate</button>'))
            page = context.new_page()
            page.set_default_timeout(500)
            page.set_content("""<label for="code">Six-character code</label><input id="code" oninput="if(this.value.length===6)document.body.innerHTML='&lt;p&gt;Code rejected&lt;/p&gt;'">""")
            with patch.dict(os.environ, {'LOGIN_FLOW': 'recorded', 'MFA_AUTO_SUBMIT': 'true'}):
                with self.assertRaises(BrowserTimeoutError):
                    complete_mfa(page, '123456')
            self.assertEqual(page.locator('h1').inner_text(), 'Please sign in')
            self.assertEqual(page.get_by_role('link', name='Brave Threshing').count(), 0)
        finally:
            context.close()

    def test_forwarded_mail_is_fetched_and_entered_automatically_without_starting_game(self):
        context = self.browser.new_context()
        def site(route):
            request = route.request
            path = __import__('urllib.parse', fromlist=['urlsplit']).urlsplit(request.url).path
            if request.method == 'POST':
                body = """<label for="code">Six-character code</label><input id="code" oninput="if(this.value.length===6)location.href='/rotunda'">"""
            elif path == '/rotunda':
                body = '<p id="cooldown-label">You can brave Threshing again in</p><button popovertarget="profile">Profile</button><div id="profile" popover></div>'
            else:
                body = '<form method="post" action="/enter"><label for="reader">Email Address</label><input id="reader" name="reader"><button aria-label="Send me a code">→</button></form>'
            route.fulfill(content_type='text/html', body=body)
        context.route('https://dragonkind.com/**', site)
        try:
            page = context.new_page()
            page.set_default_timeout(2000)
            with patch.dict(os.environ, {'LOGIN_FLOW': 'recorded', 'LOGIN_EMAIL_CASE': 'original', 'MFA_AUTO_SUBMIT': 'true'}), \
                 patch('dragonbot.__main__.inbox') as mailbox, \
                 patch('dragonbot.__main__.highwater', return_value=42), \
                 patch('dragonbot.__main__.wait_code', return_value='123456') as code_reader, \
                 patch('dragonbot.__main__.save_state') as save:
                status = mail_login_page(page, 'ralphleggejr@gmail.com')
                self.assertEqual(status, 'cooldown')
                code_reader.assert_called_once_with(mailbox.return_value.__enter__.return_value, 42, 'ralphleggejr@gmail.com')
                save.assert_not_called()
                self.assertEqual(page.url, 'https://dragonkind.com/rotunda')
                self.assertIsNone(page.locator('body').get_attribute('data-clicked'))
        finally:
            context.close()

    def test_manual_replay_recognizes_progressed_authenticated_game_page(self):
        context = self.browser.new_context()
        try:
            context.route('https://dragonkind.com/play', lambda route: route.fulfill(content_type='text/html',
                body='<button popovertarget="profile">Profile</button><form id="choices"><input id="choice-0" type="radio"><label for="choice-0">Head deeper into the forest</label></form>'))
            page = context.new_page()
            page.set_default_timeout(1000)
            page.goto('https://dragonkind.com/play')
            with patch.dict(os.environ, {'LOGIN_FLOW': 'recorded'}):
                self.assertEqual(confirm_login(page, allow_existing_session=True), 'in_progress')
                self.assertEqual(page.url, 'https://dragonkind.com/play')
                self.assertFalse(page.locator('#choice-0').is_checked())
        finally:
            context.close()

    def test_login_diagnostic_excludes_codes_tokens_and_query_strings(self):
        context = self.browser.new_context()
        try:
            context.route('https://dragonkind.com/**', lambda route: route.fulfill(content_type='text/html', body=
                '<label for="code">Six-character code</label><input id="code" value="SECRET_CODE">'
                '<input type="hidden" name="_csrf_token" value="SECRET_TOKEN"><span class="cell">SECRET_CELL</span>'))
            page = context.new_page()
            page.goto('https://dragonkind.com/code?token=SECRET_QUERY#SECRET_FRAGMENT')
            with patch.dict(os.environ, {'LOGIN_FLOW': 'recorded'}):
                diagnostic = login_page_status(page)
            self.assertEqual(diagnostic['path'], '/code')
            self.assertTrue(diagnostic['controls']['verification_code']['visible'])
            self.assertFalse(diagnostic['controls']['brave_threshing_link']['visible'])
            self.assertNotIn('SECRET', json.dumps(diagnostic))
        finally:
            context.close()

    def exercise_login_timeout(self, verify_code):
        context = self.browser.new_context()
        def site(route):
            if route.request.method == 'POST':
                action = 'oninput="if(this.value.length===6)location.href=\'/rotunda\'"' if verify_code else ''
                body = f'<label for="code">Six-character code</label><input id="code" {action}>'
            elif route.request.url.endswith('/rotunda'):
                body = '<button popovertarget="profile">Profile</button><div id="profile" popover></div>'
            else:
                body = '<form method="post" action="/enter"><label for="email">Email Address</label><input id="email" name="reader"><button aria-label="Send me a code">→</button></form>'
            route.fulfill(content_type='text/html', body=body)
        context.route('https://dragonkind.com/**', site)
        browser = MagicMock()
        browser.new_context.return_value = context
        account = ROUTE['accounts'][0]
        state = {'accounts': {}, 'global_pause': False}
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'LOGIN_FLOW': 'recorded', 'LOGIN_EMAIL_CASE': 'original',
                                        'PAGE_LOAD_TIMEOUT_SECONDS': '1', 'MFA_AUTO_SUBMIT': 'true'}), \
                 patch('dragonbot.__main__.STATE', Path(directory) / 'state.json'), \
                 patch('dragonbot.__main__.inbox'), \
                 patch('dragonbot.__main__.highwater', return_value=42), \
                 patch('dragonbot.__main__.wait_code', return_value='ABC123'), \
                 patch('dragonbot.__main__.notify'), \
                 patch('playwright.sync_api.Page.screenshot'):
                play(browser, ROUTE, state, account)
                saved = json.loads((Path(directory) / 'state.json').read_text())
        self.assertTrue(saved['global_pause'])
        self.assertNotIn('ABC123', json.dumps(saved))
        return saved['accounts'][account]

    def test_verification_timeout_identifies_code_form_stage(self):
        record = self.exercise_login_timeout(verify_code=False)
        self.assertEqual(record['reason'], 'Failure while waiting for the verification form to close (TimeoutError)')
        self.assertTrue(record['login_diagnostic']['controls']['verification_code']['visible'])

    def test_rotunda_panel_timeout_is_not_reported_as_failed_mfa(self):
        record = self.exercise_login_timeout(verify_code=True)
        self.assertEqual(record['reason'], 'Failure while checking the story start or cooldown panel (TimeoutError)')
        self.assertEqual(record['login_diagnostic']['path'], '/rotunda')
        self.assertTrue(record['login_diagnostic']['controls']['profile']['visible'])
        self.assertFalse(record['login_diagnostic']['controls']['verification_code']['visible'])

    def test_account_with_site_cooldown_is_saved_signed_out_and_skipped(self):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_default_timeout(2000)
            page.set_content("""<section class="cooldown"><p id="cooldown-label">You can brave Threshing again in</p><time data-countdown datetime="1970-01-01T01:00:00+00:00">1:00</time></section><button popovertarget="profile">Profile</button><div id="profile" popover><button id="signout" onclick="document.body.innerHTML='&lt;label for=email&gt;Email Address&lt;/label&gt;&lt;input id=email&gt;'">Sign out</button></div>""")
            state = {'accounts': {'one@gmail.com': {'status': 'active', 'next_run': 0}}, 'global_pause': False}
            with patch.dict(os.environ, {'LOGIN_FLOW': 'recorded', 'PROFILE_SELECTOR': 'button[popovertarget=profile]',
                                        'SIGN_OUT_SELECTOR': '#signout', 'LOGIN_ENTRY_SELECTOR': '',
                                        'COOLDOWN_SELECTOR': '#cooldown-label'}), \
                 patch('dragonbot.__main__.time.time', return_value=1000), \
                 patch('dragonbot.__main__.save_state'):
                self.assertFalse(account_ready_for_story(page, {'cooldown_seconds': 14400}, state, 'one@gmail.com', 'cooldown'))
                self.assertEqual(state['accounts']['one@gmail.com']['next_run'], 3600)
                self.assertEqual(state['accounts']['one@gmail.com']['status'], 'cooldown')
                self.assertFalse(state['global_pause'])
                self.assertTrue(page.get_by_role('textbox', name='Email Address').is_visible())
        finally:
            context.close()

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from .core import choose, load_state, save_state, shadow, login_request_summary, refresh_global_pause, retry_login_rejections, normalized, locate_story_step, resume_story_failures, StoryPositionError
from .mail import highwater, inbox, notify, wait_code

STATE = Path('data/state.json')
REQUIRED = ['MAIL_USER', 'MAIL_PASSWORD', 'EMAIL_SELECTOR', 'EMAIL_SUBMIT_SELECTOR',
            'MFA_SELECTOR', 'AUTH_READY_SELECTOR', 'CHOICES_SELECTOR', 'CHOICE_SUBMIT_SELECTOR', 'DEATH_SELECTOR',
            'SIGN_OUT_SELECTOR', 'MFA_ORIGINAL_SENDER']


def page_timeout_ms():
    try:
        seconds = int(os.environ.get('PAGE_LOAD_TIMEOUT_SECONDS', '300'))
    except ValueError:
        raise RuntimeError('PAGE_LOAD_TIMEOUT_SECONDS must be a positive integer') from None
    if seconds <= 0:
        raise RuntimeError('PAGE_LOAD_TIMEOUT_SECONDS must be a positive integer')
    return min(seconds, 300) * 1000


def configure_page(page):
    timeout = page_timeout_ms()
    page.set_default_timeout(timeout)
    page.set_default_navigation_timeout(timeout)


def email_typing_delay_ms():
    try:
        delay = int(os.environ.get('EMAIL_TYPING_DELAY_MS', '100'))
    except ValueError:
        raise RuntimeError('EMAIL_TYPING_DELAY_MS must be between 0 and 1000') from None
    if not 0 <= delay <= 1000:
        raise RuntimeError('EMAIL_TYPING_DELAY_MS must be between 0 and 1000')
    return delay


def configuration():
    page_timeout_ms()
    email_typing_delay_ms()
    if os.environ.get('LOGIN_FLOW', 'recorded') not in ('recorded', 'custom'):
        raise RuntimeError('LOGIN_FLOW must be recorded or custom')
    if os.environ.get('LOGIN_EMAIL_CASE', 'original') not in ('upper', 'original'):
        raise RuntimeError('LOGIN_EMAIL_CASE must be upper or original')
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        raise RuntimeError('Missing configuration: ' + ', '.join(missing))
    if os.environ.get('DEATH_NARRATIVE_SELECTOR') and not os.environ.get('DEATH_PAGE_NEXT_SELECTOR'):
        raise RuntimeError('DEATH_PAGE_NEXT_SELECTOR is required for death narrative navigation')
    auto = os.environ.get('MFA_AUTO_SUBMIT', 'true').lower()
    if auto not in ('true', 'false'):
        raise RuntimeError('MFA_AUTO_SUBMIT must be true or false')
    if auto == 'false' and not os.environ.get('MFA_SUBMIT_SELECTOR'):
        raise RuntimeError('MFA_SUBMIT_SELECTOR is required when MFA_AUTO_SUBMIT=false')
    scope = os.environ.get('SHADOW_PAUSE_SCOPE', 'account')
    if scope not in ('global', 'account'):
        raise RuntimeError('SHADOW_PAUSE_SCOPE must be global or account')
    return json.loads(Path('route.json').read_text())


def visible(page, name):
    return page.locator(os.environ[name]).filter(visible=True)


def wait_rendered(page, locator):
    locator.wait_for(state='visible')
    element = locator.element_handle()
    try:
        page.wait_for_function("""(element) => {
            if (!element?.isConnected) return false;
            for (let node = element; node; node = node.parentElement) {
                const style = getComputedStyle(node);
                if (style.visibility !== 'visible' || style.display === 'none' ||
                    Number(style.opacity) < 0.99 || node.hidden || node.inert) return false;
            }
            return true;
        }""", arg=element)
    finally:
        if element is not None:
            element.dispose()


RECORDED_LOGIN_ROLES = {
    'LOGIN_ENTRY_SELECTOR': ('button', 'Welcome, Candidate'),
    'EMAIL_SELECTOR': ('textbox', 'Email Address'),
    'EMAIL_SUBMIT_SELECTOR': ('button', 'Send me a code'),
    'MFA_SELECTOR': ('textbox', 'Six-character code'),
    'AUTH_READY_SELECTOR': ('link', 'Brave Threshing'),
}


def login_locator(page, name):
    if os.environ.get('LOGIN_FLOW', 'recorded') == 'recorded' and name in RECORDED_LOGIN_ROLES:
        role, label = RECORDED_LOGIN_ROLES[name]
        if name == 'AUTH_READY_SELECTOR':
            return page.get_by_role(role, name=label, exact=True).or_(
                page.get_by_role('link', name='Continue your story', exact=True)).filter(visible=True)
        return page.get_by_role(role, name=label, exact=True)
    return page.locator(os.environ[name])


def story_controls(page):
    controls = page.locator(os.environ['CHOICES_SELECTOR'])
    if os.environ.get('LOGIN_FLOW', 'recorded') == 'recorded':
        for role, name in (('link', 'Brave Threshing'), ('button', 'Brave Threshing'),
                           ('link', 'Continue your story'), ('button', 'Continue your story'),
                           ('button', 'Continue')):
            controls = controls.or_(page.get_by_role(role, name=name, exact=True))
    return controls.filter(visible=True)


PAINTED_ELEMENT_JS = """(element) => {
    if (!element?.isConnected || !element.getClientRects().length) return false;
    for (let node = element; node; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (style.visibility !== 'visible' || style.display === 'none' ||
            Number(style.opacity) <= 0 || node.hidden || node.inert) return false;
    }
    return true;
}"""


def story_control_snapshot(page, controls=None):
    controls = story_controls(page) if controls is None else controls
    return controls.evaluate_all("""(elements) => {
        const painted = """ + PAINTED_ELEMENT_JS + """;
        return elements.map((element, index) => ({
            index, tag: element.tagName, text: element.innerText.trim(),
            control: element.tagName === 'LABEL' ? element.getAttribute('for') : null,
            painted: painted(element)
        }));
    }""")


def read_story_controls(page):
    if os.environ.get('LOGIN_FLOW', 'recorded') == 'recorded':
        # Re-evaluate live DOM/styles: layout visibility alone also includes opacity:0.
        page.wait_for_function("""() => {
            const painted = """ + PAINTED_ELEMENT_JS + """;
            return [...document.querySelectorAll('label[for^="choice-"], button, a')].some(element =>
                painted(element) && (element.matches('label[for^="choice-"]') ||
                ['continue', 'brave threshing', 'continue your story'].includes(element.innerText.trim().toLowerCase())));
        }""")
    rows = [row for row in story_control_snapshot(page) if row['painted']]
    labels = [row for row in rows if row['tag'] == 'LABEL']
    if labels:
        # Each choice can reveal separately. Include every current option in shadow checks.
        label = story_controls(page).nth(labels[0]['index'])
        group = label.evaluate_handle("(element) => element.control?.form || element.closest('form') || element.parentElement")
        try:
            page.wait_for_function("""(group) => {
                const painted = """ + PAINTED_ELEMENT_JS + """;
                const labels = [...group.querySelectorAll('label[for^="choice-"]')].filter(element =>
                    element.getClientRects().length && getComputedStyle(element).visibility === 'visible');
                return labels.length > 0 && labels.every(painted);
            }""", arg=group)
        finally:
            group.dispose()
        rows = [row for row in story_control_snapshot(page) if row['painted']]
    return rows


def story_page_status(page):
    try:
        path = urlsplit(page.url).path
        return {'path': path if path in ('/rotunda', '/archway', '/play') else '[other]',
                'controls': [row for row in story_control_snapshot(page, page.locator('a, button, label[for^="choice-"]').filter(visible=True))
                             if row['tag'] in ('A', 'BUTTON', 'LABEL')]}
    except Exception:
        return {'available': False}


def prepare_login(page):
    email = login_locator(page, 'EMAIL_SELECTOR')
    entry_selector = os.environ.get('LOGIN_ENTRY_SELECTOR')
    if entry_selector or os.environ.get('LOGIN_FLOW', 'recorded') == 'recorded':
        entry = login_locator(page, 'LOGIN_ENTRY_SELECTOR')
        email.or_(entry).filter(visible=True).first.wait_for(state='visible')
        if not email.is_visible():
            wait_rendered(page, entry)
            entry.click()
    wait_rendered(page, email)


def fill_login_email(page, account):
    submitted = account.upper() if os.environ.get('LOGIN_EMAIL_CASE', 'original') == 'upper' else account
    field = login_locator(page, 'EMAIL_SELECTOR')
    wait_rendered(page, field)
    if os.environ.get('LOGIN_FLOW', 'recorded') == 'recorded':
        print('Entering login email using the recorded click/fill sequence.', flush=True)
        field.click()
        field.fill(submitted)
    else:
        print('Entering login email with paced keystrokes.', flush=True)
        field.fill('')
        field.press_sequentially(submitted, delay=email_typing_delay_ms())
        field.press('Tab')
    if field.input_value() != submitted:
        raise RuntimeError('Email field does not contain the configured account')
    return submitted


class LoginRequestRejected(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f'HTTP {status}')


def submit_login_code_request(page):
    field = login_locator(page, 'EMAIL_SELECTOR')
    native_post = field.evaluate('(e) => e.form?.method.toLowerCase() === "post"')
    button = login_locator(page, 'EMAIL_SUBMIT_SELECTOR').filter(visible=True)
    wait_rendered(page, button)
    # Check actionability before the single actual arrow click; trial does not submit.
    button.click(trial=True)
    print('Email entered; clicking the Send me a code arrow.', flush=True)
    if native_post:
        with page.expect_navigation(wait_until='domcontentloaded') as navigation:
            button.click(no_wait_after=True)
        response = navigation.value
        if response is not None:
            print(f'Login form response: HTTP {response.status}', flush=True)
            if response.status >= 400:
                raise LoginRequestRejected(response.status)
    else:
        button.click()


def request_login_email(page, account, before_request=None, restart_page=None):
    for attempt in range(2):
        submitted = fill_login_email(page, account)
        boundary = before_request() if before_request else None
        print(f'Requesting login code for {submitted} (attempt {attempt + 1}/2).', flush=True)
        try:
            submit_login_code_request(page)
            return boundary
        except LoginRequestRejected as error:
            if error.status != 422 or attempt == 1:
                raise
            if restart_page:
                print('Login rejected with HTTP 422; closing the browser before one fresh retry.', flush=True)
                page = restart_page()
            else:
                print('Login rejected with HTTP 422; reloading the login form for one retry.', flush=True)
                page.goto('https://dragonkind.com', wait_until='domcontentloaded')
                prepare_login(page)


def click_transition(page, target):
    wait_rendered(page, target)
    before = page.locator('body').inner_text()
    url = page.url
    target.click()
    page.wait_for_function('(old) => document.body.innerText !== old.text || location.href !== old.url',
                           arg={'text': before, 'url': url})
    # Missing or ambiguous choices after a transition fail closed.
    page.wait_for_load_state('domcontentloaded')


def execute_choice(page, selection):
    if selection.evaluate('(element) => element.tagName') != 'LABEL':
        click_transition(page, selection)
        return
    control_id = selection.get_attribute('for')
    if not control_id:
        raise RuntimeError('Choice label has no associated input')
    control = page.evaluate("""(id) => {
        const input = document.getElementById(id);
        return input && input.tagName === 'INPUT' && input.type === 'radio' && !input.disabled;
    }""", control_id)
    if not control:
        raise RuntimeError('Choice label does not identify an enabled radio input')
    # The site intentionally dims unselected labels when a saved radio is checked.
    # Playwright still waits for the label to be visible, stable, and clickable.
    selection.click()
    page.wait_for_function('(id) => document.getElementById(id)?.checked === true', arg=control_id)
    # Selection alone does not advance the story; submit only after it is checked.
    submit = visible(page, 'CHOICE_SUBMIT_SELECTOR')
    submit.first.wait_for(state='visible')
    if submit.count() != 1:
        raise RuntimeError('Expected one visible choice submit button')
    click_transition(page, submit)


def report_login_progress(stage, progress=None):
    if progress is not None:
        progress(stage)
    print(f'Login: {stage}.', flush=True)


def login_page_status(page):
    # Inspect structure only: never collect input values, body text, tokens, or query strings.
    try:
        path = urlsplit(page.url).path
        controls = {
            'email': login_locator(page, 'EMAIL_SELECTOR'),
            'verification_code': login_locator(page, 'MFA_SELECTOR'),
            'brave_threshing_link': login_locator(page, 'AUTH_READY_SELECTOR'),
            'continue_story_link': page.get_by_role('link', name='Continue your story', exact=True),
            'cooldown': page.locator(os.environ.get('COOLDOWN_SELECTOR') or '#cooldown-label'),
            'profile': page.locator('button[popovertarget="profile"]'),
            'brave_threshing_button': page.get_by_role('button', name='Brave Threshing', exact=True),
            'continue_story_button': page.get_by_role('button', name='Continue your story', exact=True),
            'story_choices': page.locator('form#choices label[for^="choice-"]'),
        }
        return {
            'path': path if path in ('/', '/enter', '/code', '/rotunda', '/archway', '/play') else '[other]',
            'document_ready': page.evaluate('document.readyState'),
            'controls': {name: {'matches': locator.count(), 'visible': locator.filter(visible=True).count() > 0}
                         for name, locator in controls.items()},
        }
    except Exception:
        return {'available': False}


def complete_mfa(page, code, allow_existing_session=False, progress=None):
    report_login_progress('entering the verification code', progress)
    field = login_locator(page, 'MFA_SELECTOR')
    field.fill('')
    # Real typing emits input and keyboard events used by auto-submit forms.
    field.press_sequentially(code)
    if os.environ.get('MFA_AUTO_SUBMIT', 'true').lower() == 'false':
        page.locator(os.environ['MFA_SUBMIT_SELECTOR']).click()
    return confirm_login(page, allow_existing_session=allow_existing_session, progress=progress)


def confirm_login(page, allow_existing_session=False, progress=None):
    # The URL alone never proves authentication; require a logged-in UI marker.
    report_login_progress('waiting for the verification form to close', progress)
    login_locator(page, 'MFA_SELECTOR').wait_for(state='hidden')
    recorded = os.environ.get('LOGIN_FLOW', 'recorded') == 'recorded'
    authenticated_paths = ('/rotunda', '/archway', '/play')
    if recorded and urlsplit(page.url).path not in authenticated_paths:
        report_login_progress('opening Rotunda after verification', progress)
        page.goto('https://dragonkind.com/rotunda', wait_until='domcontentloaded')
    report_login_progress('waiting for authenticated page controls', progress)
    ready = login_locator(page, 'AUTH_READY_SELECTOR')
    if not (recorded and allow_existing_session):
        ready.wait_for(state='visible')
        return 'ready'
    cooldown = page.locator('#cooldown-label')
    profile = page.locator('button[popovertarget="profile"]')
    archway = page.get_by_role('button', name='Brave Threshing', exact=True).or_(
        page.get_by_role('button', name='Continue your story', exact=True)).filter(visible=True)
    story = page.locator('form#choices label[for^="choice-"]')
    ready.or_(cooldown).or_(profile).or_(archway).or_(story).filter(visible=True).first.wait_for(state='visible')
    current = urlsplit(page.url)
    if current.hostname != 'dragonkind.com' or current.path not in authenticated_paths:
        raise RuntimeError('Expected an authenticated Dragonkind page')
    if cooldown.is_visible():
        return 'cooldown'
    if ready.is_visible():
        return 'ready'
    if current.path == '/archway' and archway.count() == 1:
        return 'archway'
    return 'in_progress' if current.path in ('/archway', '/play') else 'authenticated'


def wait_story_ready(page, allow_result=False):
    ready = story_controls(page).or_(page.locator(os.environ['DEATH_SELECTOR']))
    if allow_result:
        ready = ready.or_(page.get_by_role('link', name='Continue', exact=True))
    if os.environ.get('DEATH_NARRATIVE_SELECTOR'):
        ready = ready.or_(page.locator(os.environ['DEATH_NARRATIVE_SELECTOR']))
    ready.filter(visible=True).first.wait_for(state='visible')


def finish_known_death_pages(page):
    # Narrative pages are a route to confirmation, not themselves proof of death.
    for step in range(3):
        if visible(page, 'DEATH_SELECTOR').count():
            return True
        narrative_selector = os.environ.get('DEATH_NARRATIVE_SELECTOR')
        if not narrative_selector or not page.locator(narrative_selector).filter(visible=True).count():
            if step:
                raise RuntimeError('Unrecognized page during death confirmation')
            return False
        onward = visible(page, 'DEATH_PAGE_NEXT_SELECTOR')
        onward.first.wait_for(state='visible')
        if onward.count() != 1:
            raise RuntimeError('Expected one Continue on a known death narrative page')
        click_transition(page, onward)
    raise RuntimeError('Known death narrative did not reach a result screen')


def site_cooldown_deadline(page, fallback_seconds):
    label = page.locator(os.environ.get('COOLDOWN_SELECTOR') or '#cooldown-label').filter(visible=True).first
    timestamp = label.evaluate("""(element) => {
        const container = element.closest('.cooldown') || element.closest('section') || element.parentElement;
        return container.querySelector('[data-countdown][datetime]')?.getAttribute('datetime') || null;
    }""")
    now = time.time()
    if timestamp:
        try:
            deadline = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            if deadline.tzinfo is not None:
                return max(now + 60, deadline.timestamp())
        except ValueError:
            pass
    # Preserve the four-hour rule when the site exposes no readable deadline.
    return now + fallback_seconds


def logout_from_profile(page):
    if os.environ.get('PROFILE_SELECTOR'):
        profile = visible(page, 'PROFILE_SELECTOR')
        wait_rendered(page, profile)
        profile.click()
    sign_out = page.locator(os.environ['SIGN_OUT_SELECTOR']).filter(visible=True)
    wait_rendered(page, sign_out)
    sign_out.click()
    prepare_login(page)


def account_ready_for_story(page, route, state, account, login_status):
    if os.environ.get('LOGIN_FLOW', 'recorded') != 'recorded':
        return True
    ready = login_locator(page, 'AUTH_READY_SELECTOR')
    cooldown = page.locator(os.environ.get('COOLDOWN_SELECTOR') or '#cooldown-label')
    if login_status in ('in_progress', 'archway'):
        print('Opening Rotunda to use the recorded story entry path.', flush=True)
        page.goto('https://dragonkind.com/rotunda', wait_until='domcontentloaded')
        login_status = 'authenticated'
    # Profile can appear before the animated ready/cooldown panel; wait for that panel.
    if login_status == 'authenticated':
        print('Login confirmed; waiting for Brave Threshing, Continue your story, or the cooldown panel.', flush=True)
        ready.or_(cooldown).filter(visible=True).first.wait_for(state='visible')
    if cooldown.is_visible():
        record = state['accounts'][account]
        record.update(status='cooldown', next_run=max(record.get('next_run', 0),
                      site_cooldown_deadline(page, route['cooldown_seconds'])), step=0)
        save_state(STATE, state)
        logout_from_profile(page)
        print(f'{account} is already cooling down; moving to the next account.', flush=True)
        return False
    ready.wait_for(state='visible')
    return True


def inspect_resumed_story(page, route, state, account):
    # Continue-only pages reveal narrative; only a unique recorded choice identifies position.
    for revealed in range(4):
        result = page.get_by_role('link', name='Continue', exact=True).filter(visible=True)
        terminal = visible(page, 'DEATH_SELECTOR')
        story_controls(page).or_(result).or_(terminal).first.wait_for(state='visible')
        if terminal.count():
            return len(route['steps'])
        rows = read_story_controls(page)
        choices = [row for row in rows if row['tag'] == 'LABEL']
        if choices:
            labels = [row['text'] for row in choices]
            index = locate_story_step(labels, route['steps'])
            state['accounts'][account]['step'] = index
            save_state(STATE, state)
            if index == route['shadow_checkpoint'] and shadow(labels):
                alert(state, account, 'A story option mentions shadow', page,
                      os.environ.get('SHADOW_PAUSE_SCOPE', 'account') == 'global')
                return None
            print(f'Resuming at story step {index + 1}, identified from the visible choices.', flush=True)
            return index
        results = [row for row in story_control_snapshot(page, result) if row['painted']]
        if results:
            finish_recorded_ending(page, route, state, account, allow_partial=True)
            return None
        onward_rows = [row for row in rows if row['tag'] == 'BUTTON' and normalized(row['text']) == 'continue']
        if revealed == 3 or len(onward_rows) != 1:
            raise StoryPositionError('Cannot identify the resumed story within three Continue pages')
        onward = story_controls(page).nth(onward_rows[0]['index'])
        print(f'Resumed story: revealing Continue page {revealed + 1}/3.', flush=True)
        click_transition(page, onward)
    raise StoryPositionError('Cannot identify the resumed story')


def finish_recorded_ending(page, route, state, account, allow_partial=False):
    # The supplied endings have two or three Continue buttons, then a Rotunda link.
    # The site's actual cooldown panel, rather than these buttons, proves completion.
    record = state['accounts'][account]
    for count in range(4):
        result = page.get_by_role('link', name='Continue', exact=True).filter(visible=True)
        rows = read_story_controls(page)
        onward_rows = [row for row in rows if row['tag'] == 'BUTTON' and normalized(row['text']) == 'continue']
        if any(row['tag'] == 'LABEL' for row in rows):
            raise StoryPositionError('Unexpected choice during the recorded ending')
        result_rows = [row for row in story_control_snapshot(page, result) if row['painted']]
        if result_rows:
            if (not allow_partial and count not in (2, 3)) or len(result_rows) != 1 or onward_rows:
                raise StoryPositionError('Unrecognized recorded ending')
            result = result.nth(result_rows[0]['index'])
            target = urlsplit(urljoin(page.url, result.get_attribute('href') or ''))
            if target.scheme != 'https' or target.hostname != 'dragonkind.com' or target.path != '/rotunda':
                raise StoryPositionError('Recorded result link must lead to Rotunda')
            wait_rendered(page, result)
            click_transition(page, result)
            cooldown = page.locator(os.environ.get('COOLDOWN_SELECTOR') or '#cooldown-label')
            wait_rendered(page, cooldown)
            record.update(status='logging_out', next_run=max(record.get('next_run', 0),
                          time.time() + route['cooldown_seconds']))
            save_state(STATE, state)
            logout_from_profile(page)
            record.update(status='cooldown', step=0)
            save_state(STATE, state)
            print(f'Recorded ending and site cooldown confirmed for {account}; cooldown is four hours.', flush=True)
            return
        if count == 3 or len(onward_rows) != 1:
            raise StoryPositionError('Recorded ending exceeded its allowed Continue pages')
        onward = story_controls(page).nth(onward_rows[0]['index'])
        record['step'] = len(route['steps']) + count
        save_state(STATE, state)
        wait_rendered(page, onward)
        click_transition(page, onward)


def logout_after_death(page):
    # This function must never use the ordinary Continue button as proof of death.
    if not visible(page, 'DEATH_SELECTOR').count():
        raise RuntimeError('Logout sequence requires a confirmed death screen')
    for name in ('DEATH_CONTINUE_SELECTOR', 'ROTUNDA_SELECTOR'):
        if os.environ.get(name):
            click_transition(page, visible(page, name))
    if os.environ.get('COOLDOWN_SELECTOR'):
        page.locator(os.environ['COOLDOWN_SELECTOR']).wait_for(state='visible')
    logout_from_profile(page)


def alert(state, account, reason, page=None, global_pause=True):
    record = state['accounts'].setdefault(account, {})
    record.update(status='paused', reason=reason, notification_pending=True,
                  pause_scope='global' if global_pause else 'account')
    state['global_pause'] = state['global_pause'] or global_pause
    save_state(STATE, state)  # Persist the pause even if screenshot or mail fails.
    print(f'Paused {account}: {reason}. Scope: {record["pause_scope"]}.', flush=True)
    if page is not None:
        try:
            page.screenshot(path=f'data/{account}-paused.png', full_page=True, animations='disabled')
        except Exception:
            pass
    try:
        notify('Dragonkind automation paused',
               f'Account: {account}\nReason: {reason}\nStep: {record.get("step", 0)}\n'
               'Review the local screenshot and state. No further choices will be made for this paused scope.')
        record['notification_pending'] = False
        save_state(STATE, state)
    except Exception:
        print('Notification could not be delivered. Pause saved; check mail settings.', flush=True)


def play(browser, route, state, account, restart_browser=None):
    record = state['accounts'].setdefault(account, {})
    context = browser.new_context()
    page = context.new_page()
    configure_page(page)
    authenticated = False
    story_started = False
    phase = 'loading the page'
    def login_progress(stage):
        nonlocal phase
        phase = stage
    def restart_login_page():
        nonlocal context, page
        context.close()
        fresh_browser = restart_browser()
        context = fresh_browser.new_context()
        page = context.new_page()
        configure_page(page)
        page.goto('https://dragonkind.com', wait_until='domcontentloaded')
        prepare_login(page)
        return page
    try:
        record.update(status='active', step=0)
        record.pop('login_diagnostic', None)
        record.pop('story_diagnostic', None)
        save_state(STATE, state)
        print(f'Loading Dragonkind; waiting up to {page_timeout_ms() // 1000}s for login controls.', flush=True)
        page.goto('https://dragonkind.com', wait_until='domcontentloaded')
        phase = 'waiting for login controls'
        prepare_login(page)
        print('Login controls are ready.', flush=True)
        # Establish the UID boundary before requesting a new code. Old messages are ignored.
        phase = 'connecting to Gmail inbox'
        with inbox() as client:
            phase = 'submitting the login email'
            boundary = request_login_email(page, account, before_request=lambda: highwater(client),
                                           restart_page=restart_login_page if restart_browser else None)
            phase = 'waiting for the code-entry field'
            print('Send-code button clicked; waiting for the code-entry field.', flush=True)
            login_locator(page, 'MFA_SELECTOR').wait_for(state='visible')
            print(f'Forward the new verification email for {account} to the configured inbox.', flush=True)
            phase = 'waiting for the verification email'
            code = wait_code(client, boundary, account)
        phase = 'completing login'
        login_status = complete_mfa(page, code, allow_existing_session=True, progress=login_progress)
        authenticated = True
        print(f'Login confirmed ({login_status}) for {account}.', flush=True)
        phase = 'checking the story start or cooldown panel'
        if not account_ready_for_story(page, route, state, account, login_status):
            return
        story_started = True
        phase = 'following the story route'
        index = 0
        resume_pending = False
        while index < len(route['steps']):
            if resume_pending and index > 0:
                phase = 'inspecting the resumed story'
                wait_story_ready(page, allow_result=True)
                entry = page.get_by_role('button', name='Continue your story', exact=True).filter(visible=True)
                if index != 1 or not entry.count():
                    start = inspect_resumed_story(page, route, state, account)
                    if start is None:
                        return
                    index = start
                    resume_pending = False
                    if index == len(route['steps']):
                        break
                phase = 'following the story route'
            options = route['steps'][index]
            record['step'] = index
            save_state(STATE, state)
            wait_story_ready(page)
            if finish_known_death_pages(page):
                break
            rows = read_story_controls(page)
            labels = [row['text'] for row in rows]
            if index == route['shadow_checkpoint'] and shadow(labels):
                alert(state, account, 'A story option mentions shadow', page,
                      os.environ.get('SHADOW_PAUSE_SCOPE', 'account') == 'global')
                return
            selection = choose(labels, options, allow_contains=options == ['Hope for the best'])
            print(f'Story step {index + 1}: {labels[selection].strip()}', flush=True)
            if index < 2 and normalized(labels[selection]) == 'continue your story':
                resume_pending = True
            execute_choice(page, story_controls(page).nth(rows[selection]['index']))
            index += 1
        wait_story_ready(page)
        if not visible(page, 'DEATH_SELECTOR').count():
            if route.get('recorded_ending'):
                phase = 'following the recorded ending and confirming site cooldown'
                finish_recorded_ending(page, route, state, account)
                return
            if not finish_known_death_pages(page):
                raise RuntimeError('Route ended without a confirmed death screen')
        # Start cooldown on confirmation, even if logout subsequently fails.
        record.update(next_run=time.time() + route['cooldown_seconds'], status='logging_out')
        save_state(STATE, state)
        phase = 'signing out after death'
        logout_after_death(page)
        record.update(status='cooldown', step=0)
        save_state(STATE, state)
        print(f'Confirmed death and logout for {account}; cooldown is four hours.', flush=True)
    except Exception as error:
        if not story_started:
            record['login_diagnostic'] = login_page_status(page)
            print('Login page diagnostic: ' + json.dumps(record['login_diagnostic']), flush=True)
        else:
            record['story_diagnostic'] = story_page_status(page)
            print('Story page diagnostic: ' + json.dumps(record['story_diagnostic']), flush=True)
        # Exception text can include sensitive DOM/input values; retain only its class.
        detail = f'{type(error).__name__}; {error}' if isinstance(error, (LoginRequestRejected, StoryPositionError)) else type(error).__name__
        alert(state, account, f'Failure while {phase} ({detail})',
              page if authenticated or isinstance(error, LoginRequestRejected) else None)
    finally:
        context.close()


def run_account(playwright, route, state, account, headless=True):
    browser = None
    def launch_fresh_browser():
        nonlocal browser
        if browser is not None:
            browser.close()
            browser = None
            print('Previous browser fully closed.', flush=True)
        browser = playwright.chromium.launch(headless=headless, args=['--disable-dev-shm-usage'])
        return browser
    try:
        play(launch_fresh_browser(), route, state, account, restart_browser=launch_fresh_browser)
    finally:
        if browser is not None:
            browser.close()
            print(f'Browser fully closed for {account}.', flush=True)


def run(headless=True, retry_rejected=False, resume_stories=False):
    from playwright.sync_api import sync_playwright
    route = configuration()
    state = load_state(STATE)
    active_accounts = route['accounts']
    refresh_global_pause(state, active_accounts)
    if retry_rejected:
        retried = retry_login_rejections(state, active_accounts)
        print('Retrying only previous pre-MFA HTTP 422 failures: ' + (', '.join(retried) or 'none'), flush=True)
    if resume_stories:
        resumed = resume_story_failures(state, active_accounts)
        print('Rechecking saved story failures against the visible site: ' + (', '.join(resumed) or 'none'), flush=True)
    save_state(STATE, state)
    print('Configured accounts: ' + ', '.join(active_accounts), flush=True)
    for account, record in state['accounts'].items():
        if account not in active_accounts:
            continue
        if record.get('status') in ('active', 'logging_out'):
            alert(state, account, 'Interrupted run; manual review required')
    for account, record in state['accounts'].items():
        if account not in active_accounts:
            continue
        if record.get('notification_pending'):
            alert(state, account, record.get('reason', 'Paused'), global_pause=record.get('pause_scope', 'global') == 'global')
    if state['global_pause']:
        print('Global pause is active. Review data/state.json and the saved screenshot.')
        return
    with sync_playwright() as playwright:
        while not state['global_pause']:
            for account in route['accounts']:
                record = state['accounts'].get(account, {})
                if record.get('status') == 'paused' or record.get('next_run', 0) > time.time():
                    continue
                run_account(playwright, route, state, account, headless=headless)
                if state['global_pause']:
                    print('Global pause is active; the remaining accounts have not been started. Review data/state.json.', flush=True)
                    break
            if all(state['accounts'].get(a, {}).get('status') == 'paused' for a in route['accounts']):
                print('All configured accounts are paused; review saved state.', flush=True)
                break
            if not state['global_pause']:
                time.sleep(15)


def inspect_login_submission(page, account):
    captured = []
    def intercept(route):
        request = route.request
        if request.method not in ('GET', 'HEAD'):
            if request.method == 'POST' and urlsplit(request.url).path == '/enter':
                captured.append(login_request_summary(request.post_data, request.all_headers(), account))
            route.abort()  # Block all mutations, including the login-code request.
        else:
            route.fallback()
    page.context.route('**/*', intercept)
    try:
        page.goto('https://dragonkind.com', wait_until='domcontentloaded')
        prepare_login(page)
        fill_login_email(page, account)
        try:
            submit_login_code_request(page)
        except Exception:
            if not captured:
                raise
        if len(captured) != 1:
            raise RuntimeError('Expected exactly one intercepted login request')
        return captured[0]
    finally:
        page.context.unroute('**/*', intercept)


def diagnose_login(account):
    from playwright.sync_api import sync_playwright
    print('Dry run: login submission is intercepted; no verification email will be requested.', flush=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=['--disable-dev-shm-usage'])
        context = browser.new_context(service_workers='block')
        try:
            page = context.new_page()
            configure_page(page)
            summary = inspect_login_submission(page, account)
            print(json.dumps(summary, indent=2), flush=True)
        finally:
            context.close()
            browser.close()


def replay_login_page(page, account):
    page.goto('https://dragonkind.com/', wait_until='domcontentloaded')
    prepare_login(page)
    fill_login_email(page, account)
    submit_login_code_request(page)
    login_locator(page, 'MFA_SELECTOR').wait_for(state='visible')


def mail_login_page(page, account):
    page.goto('https://dragonkind.com/', wait_until='domcontentloaded')
    prepare_login(page)
    with inbox() as client:
        boundary = highwater(client)
        fill_login_email(page, account)
        submit_login_code_request(page)
        login_locator(page, 'MFA_SELECTOR').wait_for(state='visible')
        print('Waiting for the new forwarded verification email; leave the browser untouched.', flush=True)
        code = wait_code(client, boundary, account)
    return complete_mfa(page, code, allow_existing_session=True)


def replay_login(account, automatic=False):
    from playwright.sync_api import sync_playwright, Error as BrowserError
    if automatic:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=Path(".env"), override=False)
        missing = [name for name in ("MAIL_USER", "MAIL_PASSWORD", "MFA_ORIGINAL_SENDER") if not os.environ.get(name)]
        if missing:
            raise RuntimeError("Missing mail settings: " + ", ".join(missing))
    # Use native headed Chromium; automatic mode reads only local mail bindings.
    os.environ['LOGIN_FLOW'] = 'recorded'
    os.environ['LOGIN_EMAIL_CASE'] = 'original'
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = None
        page = None
        try:
            context = browser.new_context()
            page = context.new_page()
            configure_page(page)
            if automatic:
                status = mail_login_page(page, account)
            else:
                replay_login_page(page, account)
                print('Enter the verification code directly in the browser. Do not click story links.', flush=True)
                status = confirm_login(page, allow_existing_session=True)
            print(f'Login confirmed ({status}). No story choices were clicked.', flush=True)
            return True
        except BrowserError as error:
            if (page is not None and page.is_closed()) or not browser.is_connected():
                print('Browser closed before login confirmation; test cancelled. Email delivery may still have succeeded.', flush=True)
                return False
            print(f'Login test stopped ({type(error).__name__}); no game choices were made.', flush=True)
            print('Login page diagnostic: ' + json.dumps(login_page_status(page)), flush=True)
            return False
        except LoginRequestRejected as error:
            print(f'Login test rejected: {error}. No game choices were made.', flush=True)
            return False
        except Exception as error:
            # Avoid third-party exception text that might contain entered codes.
            print(f'Login test stopped ({type(error).__name__}); no game choices were made.', flush=True)
            return False
        finally:
            if context is not None:
                context.close()
            browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['run', 'check', 'reset', 'diagnose-login', 'replay-login', 'desktop-login', 'run-desktop'])
    parser.add_argument('--account')
    parser.add_argument('--confirm-site-reset', action='store_true')
    parser.add_argument('--retry-login-errors', action='store_true', help='Retry only saved pre-MFA HTTP 422 rejections; retain cooldowns and other pauses.')
    parser.add_argument('--resume-stories', action='store_true', help='Recheck saved story-stage failures using the recorded entry and visible choices; retain shadow pauses and cooldowns.')
    args = parser.parse_args()
    if args.command == 'check':
        configuration()
        print('Required settings present. Live selectors, email access and site flow remain unverified.')
    elif args.command == 'run-desktop':
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=Path('.env'), override=False)
        os.environ['LOGIN_FLOW'] = 'recorded'
        os.environ['LOGIN_EMAIL_CASE'] = 'original'
        run(headless=False, retry_rejected=args.retry_login_errors, resume_stories=args.resume_stories)
    elif args.command in ('replay-login', 'desktop-login'):
        if not args.account:
            parser.error(f'{args.command} requires --account')
        if replay_login(args.account, automatic=args.command == 'desktop-login') is False:
            raise SystemExit(2)
    elif args.command == 'diagnose-login':
        if not args.account:
            parser.error('diagnose-login requires --account')
        diagnose_login(args.account)
    elif args.command == 'reset':
        route = json.loads(Path('route.json').read_text())
        if args.account not in route['accounts'] or not args.confirm_site_reset:
            parser.error('reset requires a configured --account and --confirm-site-reset')
        state = load_state(STATE)
        refresh_global_pause(state, route['accounts'])
        old = state['accounts'].get(args.account, {})
        state['accounts'][args.account] = {'next_run': old.get('next_run', 0)}
        refresh_global_pause(state, route['accounts'])
        save_state(STATE, state)
    else:
        run(retry_rejected=args.retry_login_errors, resume_stories=args.resume_stories)


if __name__ == '__main__':
    main()

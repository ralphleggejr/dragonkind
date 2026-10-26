import argparse
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit
from .core import choose, load_state, save_state, shadow, login_request_summary, refresh_global_pause
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
    if os.environ.get('LOGIN_EMAIL_CASE', 'upper') not in ('upper', 'original'):
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


def prepare_login(page):
    email = page.locator(os.environ['EMAIL_SELECTOR'])
    entry_selector = os.environ.get('LOGIN_ENTRY_SELECTOR')
    if entry_selector:
        entry = page.locator(entry_selector)
        email.or_(entry).filter(visible=True).first.wait_for(state='visible')
        if not email.is_visible():
            wait_rendered(page, entry)
            entry.click()
    wait_rendered(page, email)


def fill_login_email(page, account):
    submitted = account.upper() if os.environ.get('LOGIN_EMAIL_CASE', 'upper') == 'upper' else account
    field = page.locator(os.environ['EMAIL_SELECTOR'])
    wait_rendered(page, field)
    print('Entering login email with paced keystrokes.', flush=True)
    field.fill('')
    field.press_sequentially(submitted, delay=email_typing_delay_ms())
    field.press('Tab')  # Commit input/change/blur before submitting the form.
    if field.input_value() != submitted:
        raise RuntimeError('Email field does not contain the configured account')
    return submitted


class LoginRequestRejected(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f'HTTP {status}')


def submit_login_code_request(page):
    field = page.locator(os.environ['EMAIL_SELECTOR'])
    native_post = field.evaluate('(e) => e.form?.method.toLowerCase() === "post"')
    button = page.locator(os.environ['EMAIL_SUBMIT_SELECTOR']).filter(visible=True)
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
    selection.click()
    page.wait_for_function('(id) => document.getElementById(id)?.checked === true', arg=control_id)
    # Selection alone does not advance the story; submit only after it is checked.
    submit = visible(page, 'CHOICE_SUBMIT_SELECTOR')
    submit.first.wait_for(state='visible')
    if submit.count() != 1:
        raise RuntimeError('Expected one visible choice submit button')
    click_transition(page, submit)


def complete_mfa(page, code):
    field = page.locator(os.environ['MFA_SELECTOR'])
    field.fill('')
    # Real typing emits input and keyboard events used by auto-submit forms.
    field.press_sequentially(code)
    if os.environ.get('MFA_AUTO_SUBMIT', 'true').lower() == 'false':
        page.locator(os.environ['MFA_SUBMIT_SELECTOR']).click()
    # Confirm the authenticated page without clicking its first story choice.
    page.locator(os.environ['AUTH_READY_SELECTOR']).wait_for(state='visible')
    field.wait_for(state='hidden')


def wait_story_ready(page):
    ready = page.locator(os.environ['CHOICES_SELECTOR']).or_(page.locator(os.environ['DEATH_SELECTOR']))
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


def logout_after_death(page):
    # This function must never use the ordinary Continue button as proof of death.
    if not visible(page, 'DEATH_SELECTOR').count():
        raise RuntimeError('Logout sequence requires a confirmed death screen')
    for name in ('DEATH_CONTINUE_SELECTOR', 'ROTUNDA_SELECTOR'):
        if os.environ.get(name):
            click_transition(page, visible(page, name))
    if os.environ.get('COOLDOWN_SELECTOR'):
        page.locator(os.environ['COOLDOWN_SELECTOR']).wait_for(state='visible')
    if os.environ.get('PROFILE_SELECTOR'):
        visible(page, 'PROFILE_SELECTOR').click()
    sign_out = page.locator(os.environ['SIGN_OUT_SELECTOR']).filter(visible=True)
    sign_out.wait_for(state='visible')
    sign_out.click()
    prepare_login(page)


def alert(state, account, reason, page=None, global_pause=True):
    record = state['accounts'].setdefault(account, {})
    record.update(status='paused', reason=reason, notification_pending=True,
                  pause_scope='global' if global_pause else 'account')
    state['global_pause'] = state['global_pause'] or global_pause
    save_state(STATE, state)  # Persist the pause even if screenshot or mail fails.
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
    phase = 'loading the page'
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
            page.locator(os.environ['MFA_SELECTOR']).wait_for(state='visible')
            print(f'Forward the new verification email for {account} to the configured inbox.', flush=True)
            phase = 'waiting for the verification email'
            code = wait_code(client, boundary, account)
        phase = 'completing login'
        complete_mfa(page, code)
        authenticated = True
        phase = 'following the story route'
        for index, options in enumerate(route['steps']):
            record['step'] = index
            save_state(STATE, state)
            wait_story_ready(page)
            if finish_known_death_pages(page):
                break
            labels = visible(page, 'CHOICES_SELECTOR').all_inner_texts()
            if index == route['shadow_checkpoint'] and shadow(labels):
                alert(state, account, 'A story option mentions shadow', page,
                      os.environ.get('SHADOW_PAUSE_SCOPE', 'account') == 'global')
                return
            selection = choose(labels, options, allow_contains=index == len(route['steps']) - 1)
            execute_choice(page, visible(page, 'CHOICES_SELECTOR').nth(selection))
        wait_story_ready(page)
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
        # Exception text can include sensitive DOM/input values; retain only its class.
        detail = f'{type(error).__name__}; {error}' if isinstance(error, LoginRequestRejected) else type(error).__name__
        alert(state, account, f'Failure while {phase} ({detail})',
              page if authenticated or isinstance(error, LoginRequestRejected) else None)
    finally:
        context.close()


def run_account(playwright, route, state, account):
    browser = None
    def launch_fresh_browser():
        nonlocal browser
        if browser is not None:
            browser.close()
            browser = None
            print('Previous browser fully closed.', flush=True)
        browser = playwright.chromium.launch(headless=True, args=['--disable-dev-shm-usage'])
        return browser
    try:
        play(launch_fresh_browser(), route, state, account, restart_browser=launch_fresh_browser)
    finally:
        if browser is not None:
            browser.close()
            print(f'Browser fully closed for {account}.', flush=True)


def run():
    from playwright.sync_api import sync_playwright
    route = configuration()
    state = load_state(STATE)
    active_accounts = route['accounts']
    refresh_global_pause(state, active_accounts)
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
                run_account(playwright, route, state, account)
                if state['global_pause']:
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['run', 'check', 'reset', 'diagnose-login'])
    parser.add_argument('--account')
    parser.add_argument('--confirm-site-reset', action='store_true')
    args = parser.parse_args()
    if args.command == 'check':
        configuration()
        print('Required settings present. Live selectors, email access and site flow remain unverified.')
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
        run()


if __name__ == '__main__':
    main()

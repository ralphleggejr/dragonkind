import argparse
import json
import os
import time
from pathlib import Path
from .core import choose, load_state, save_state, shadow
from .mail import highwater, inbox, notify, wait_code

STATE = Path('data/state.json')
REQUIRED = ['MAIL_USER', 'MAIL_PASSWORD', 'EMAIL_SELECTOR', 'EMAIL_SUBMIT_SELECTOR',
            'MFA_SELECTOR', 'MFA_SUBMIT_SELECTOR', 'CHOICES_SELECTOR', 'DEATH_SELECTOR',
            'SIGN_OUT_SELECTOR', 'MFA_ORIGINAL_SENDER']


def configuration():
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        raise RuntimeError('Missing configuration: ' + ', '.join(missing))
    scope = os.environ.get('SHADOW_PAUSE_SCOPE', 'account')
    if scope not in ('global', 'account'):
        raise RuntimeError('SHADOW_PAUSE_SCOPE must be global or account')
    return json.loads(Path('route.json').read_text())


def visible(page, name):
    return page.locator(os.environ[name]).filter(visible=True)


def prepare_login(page):
    email = page.locator(os.environ['EMAIL_SELECTOR'])
    entry_selector = os.environ.get('LOGIN_ENTRY_SELECTOR')
    if entry_selector:
        entry = page.locator(entry_selector)
        email.or_(entry).filter(visible=True).first.wait_for(state='visible')
        if not email.is_visible():
            entry.click()
    email.wait_for(state='visible')


def click_transition(page, target):
    before = page.locator('body').inner_text()
    url = page.url
    target.click()
    page.wait_for_function('(old) => document.body.innerText !== old.text || location.href !== old.url',
                           arg={'text': before, 'url': url}, timeout=20000)
    # Missing or ambiguous choices after a transition fail closed.
    page.wait_for_load_state('domcontentloaded')


def alert(state, account, reason, page=None, global_pause=True):
    record = state['accounts'].setdefault(account, {})
    record.update(status='paused', reason=reason, notification_pending=True)
    state['global_pause'] = state['global_pause'] or global_pause
    save_state(STATE, state)  # Persist the pause even if screenshot or mail fails.
    if page is not None:
        try:
            page.screenshot(path=f'data/{account}-paused.png', full_page=True)
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


def play(browser, route, state, account):
    record = state['accounts'].setdefault(account, {})
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(20000)
    authenticated = False
    try:
        record.update(status='active', step=0)
        save_state(STATE, state)
        page.goto('https://dragonkind.com', wait_until='domcontentloaded')
        prepare_login(page)
        page.locator(os.environ['EMAIL_SELECTOR']).fill(account)
        # Establish the UID boundary before requesting a new code. Old messages are ignored.
        with inbox() as client:
            boundary = highwater(client)
            page.locator(os.environ['EMAIL_SUBMIT_SELECTOR']).click()
            page.locator(os.environ['MFA_SELECTOR']).wait_for(state='visible')
            print(f'Forward the new verification email for {account} to the configured inbox.', flush=True)
            code = wait_code(client, boundary, account)
        page.locator(os.environ['MFA_SELECTOR']).fill(code)
        click_transition(page, page.locator(os.environ['MFA_SUBMIT_SELECTOR']))
        authenticated = True
        for index, options in enumerate(route['steps']):
            record['step'] = index
            save_state(STATE, state)
            if visible(page, 'DEATH_SELECTOR').count():
                break
            labels = visible(page, 'CHOICES_SELECTOR').all_inner_texts()
            if index == route['shadow_checkpoint'] and shadow(labels):
                alert(state, account, 'Third option mentions shadow', page,
                      os.environ.get('SHADOW_PAUSE_SCOPE', 'account') == 'global')
                return
            selection = choose(labels, options, allow_contains=index == len(route['steps']) - 1)
            click_transition(page, visible(page, 'CHOICES_SELECTOR').nth(selection))
        if not visible(page, 'DEATH_SELECTOR').count():
            raise RuntimeError('Route ended without a confirmed death screen')
        # Start cooldown on confirmation, even if logout subsequently fails.
        record.update(next_run=time.time() + route['cooldown_seconds'], status='logging_out')
        save_state(STATE, state)
        click_transition(page, visible(page, 'SIGN_OUT_SELECTOR'))
        prepare_login(page)
        record.update(status='cooldown', step=0)
        save_state(STATE, state)
        print(f'Confirmed death and logout for {account}; cooldown is four hours.', flush=True)
    except Exception as error:
        # Exception text can include sensitive DOM/input values; retain only its class.
        alert(state, account, f'Unexpected page or operation failure ({type(error).__name__})', page if authenticated else None)
    finally:
        context.close()


def run():
    from playwright.sync_api import sync_playwright
    route = configuration()
    state = load_state(STATE)
    for account, record in state['accounts'].items():
        if record.get('status') in ('active', 'logging_out'):
            alert(state, account, 'Interrupted run; manual review required')
    for account, record in state['accounts'].items():
        if record.get('notification_pending'):
            alert(state, account, record.get('reason', 'Paused'), global_pause=state['global_pause'])
    if state['global_pause']:
        print('Global pause is active. Review data/state.json and the saved screenshot.')
        return
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=['--disable-dev-shm-usage'])
        try:
            while not state['global_pause']:
                for account in route['accounts']:
                    record = state['accounts'].get(account, {})
                    if record.get('status') == 'paused' or record.get('next_run', 0) > time.time():
                        continue
                    play(browser, route, state, account)
                    if state['global_pause']:
                        break
                if all(state['accounts'].get(a, {}).get('status') == 'paused' for a in route['accounts']):
                    break
                if not state['global_pause']:
                    time.sleep(15)
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['run', 'check', 'reset'])
    parser.add_argument('--account')
    parser.add_argument('--confirm-site-reset', action='store_true')
    args = parser.parse_args()
    if args.command == 'check':
        configuration()
        print('Required settings present. Live selectors, email access and site flow remain unverified.')
    elif args.command == 'reset':
        route = json.loads(Path('route.json').read_text())
        if args.account not in route['accounts'] or not args.confirm_site_reset:
            parser.error('reset requires a configured --account and --confirm-site-reset')
        state = load_state(STATE)
        old = state['accounts'].get(args.account, {})
        state['accounts'][args.account] = {'next_run': old.get('next_run', 0)}
        state['global_pause'] = any(r.get('status') == 'paused' for r in state['accounts'].values())
        save_state(STATE, state)
    else:
        run()


if __name__ == '__main__':
    main()

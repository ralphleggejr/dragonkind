import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs


DEFAULT_CODE_REGEX = r"(?i:your confirmation code is)\s+([0-9A-Za-z]{3}-?[0-9A-Za-z]{3})(?![0-9A-Za-z-])"

def normalized(text):
    return ' '.join(text.lower().replace('’', "'").split()).rstrip('.!?')


def matching_choices(labels, alternatives, allow_contains=False):
    def matches_alternative(label, alternative):
        text = normalized(label)
        if isinstance(alternative, dict):
            if set(alternative) != {'prefix'} or not isinstance(alternative['prefix'], str) or not alternative['prefix'].strip():
                raise RuntimeError('Invalid recorded choice prefix')
            prefix = normalized(alternative['prefix'])
            return text == prefix or text.startswith(prefix + ' ')
        wanted = normalized(alternative)
        return text == wanted or (allow_contains and wanted in text)
    return [i for i, label in enumerate(labels)
            if any(matches_alternative(label, alternative) for alternative in alternatives)]


def choose(labels, alternatives, allow_contains=False):
    matches = matching_choices(labels, alternatives, allow_contains)
    if len(matches) != 1:
        raise RuntimeError(f'Expected one route choice, found {len(matches)}')
    return matches[0]


def locate_story_step(labels, steps):
    candidates = []
    for index, alternatives in enumerate(steps[2:], 2):
        matches = matching_choices(labels, alternatives)
        if len(matches) > 1:
            raise RuntimeError('Multiple options match a recorded story step')
        if matches:
            candidates.append(index)
    if len(candidates) != 1:
        raise RuntimeError('Cannot uniquely identify the current story step')
    return candidates[0]


def shadow(labels):
    return any('shadow' in normalized(label) for label in labels)


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(state, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_state(path):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else {'accounts': {}, 'global_pause': False}


def extract_code(message, account, sender, pattern):
    # Forwarded content must identify both the original recipient and original sender.
    text = '\n'.join(part.get_content() for part in message.walk()
                     if part.get_content_type() == 'text/plain' and not part.get_content_disposition() == 'attachment')
    if not text and message.get_content_type() == 'text/html':
        return None  # Do not guess from HTML-only forwards.
    address = lambda value: re.compile(r'(?<![\w.+-])' + re.escape(value) + r'(?![\w.+-])', re.I)
    direct = address(sender).search(str(message.get('From', ''))) and address(account).search(str(message.get('To', '')))
    forwarded = (re.search(r'^To:[^\n]*' + address(account).pattern, text, re.I | re.M)
                 and re.search(r'^From:[^\n]*' + address(sender).pattern, text, re.I | re.M))
    if not (direct or forwarded):
        return None
    codes = set(re.findall(pattern, text))
    if len(codes) == 1:
        code = codes.pop()
        if isinstance(code, str):
            return code
    return None


def login_request_summary(body, headers, expected_account):
    fields = parse_qs(body or '', keep_blank_values=True)
    readers = fields.get('reader', [])
    # Only the email field is allowed to expose a value; never print token/cookie values.
    safe_readers = [value if re.fullmatch(r'[^@\s]+@[^@\s]+', value)
                    else '[not an email-shaped value]' for value in readers]
    headers = {name.lower(): value for name, value in headers.items()}
    return {
        'reader_values': safe_readers,
        'reader_matches_account_exactly': readers == [expected_account],
        'reader_matches_account_ignoring_case': len(readers) == 1 and readers[0].casefold() == expected_account.casefold(),
        'field_names': sorted(fields),
        'csrf_field_present': '_csrf_token' in fields,
        'csrf_field_nonempty': bool(fields.get('_csrf_token', [''])[0]),
        'cookie_header_present': bool(headers.get('cookie')),
        'origin_header_present': bool(headers.get('origin')),
        'referer_header_present': bool(headers.get('referer')),
    }


def refresh_global_pause(state, configured_accounts):
    # Migrate older records once, retaining their original pause scope.
    legacy_scope = 'global' if state.get('global_pause') else 'account'
    for record in state['accounts'].values():
        if record.get('status') == 'paused' and 'pause_scope' not in record:
            record['pause_scope'] = legacy_scope
    state['global_pause'] = any(
        state['accounts'].get(account, {}).get('status') == 'paused'
        and state['accounts'][account].get('pause_scope') == 'global'
        for account in configured_accounts
    )


def retry_login_rejections(state, configured_accounts):
    refresh_global_pause(state, configured_accounts)
    retried = []
    for account in configured_accounts:
        record = state['accounts'].get(account, {})
        reason = record.get('reason', '')
        if (record.get('status') == 'paused' and record.get('step') == 0
                and 'LoginRequestRejected' in reason and 'HTTP 422' in reason):
            record['previous_login_rejection'] = reason
            record['status'] = 'pending'
            record['notification_pending'] = False
            record.pop('reason', None)
            record.pop('pause_scope', None)
            retried.append(account)
    refresh_global_pause(state, configured_accounts)
    return retried


def resume_story_failures(state, configured_accounts):
    resumed = []
    phases = ('Failure while following the story route (',
              'Failure while following the recorded ending and confirming site cooldown (',
              'Failure while checking the story start or cooldown panel (',
              'Failure while inspecting the resumed story (',
              'Account already has a game in progress;')
    for account in configured_accounts:
        record = state['accounts'].get(account, {})
        reason = record.get('reason', '')
        if record.get('status') == 'paused' and reason.startswith(phases):
            record['previous_story_failure'] = reason
            record.update(status='pending', notification_pending=False)
            record.pop('reason', None)
            record.pop('pause_scope', None)
            resumed.append(account)
    refresh_global_pause(state, configured_accounts)
    return resumed

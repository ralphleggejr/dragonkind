import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs


DEFAULT_CODE_REGEX = r"(?i:your confirmation code is)\s+([0-9A-Za-z]{3}-?[0-9A-Za-z]{3})(?![0-9A-Za-z-])"

def normalized(text):
    return ' '.join(text.lower().replace('’', "'").split()).rstrip('.!?')


def choose(labels, alternatives, allow_contains=False):
    wanted = [normalized(x) for x in alternatives]
    matches = [i for i, label in enumerate(labels)
               if any(normalized(label) == x or (allow_contains and x in normalized(label)) for x in wanted)]
    if len(matches) != 1:
        raise RuntimeError(f'Expected one route choice, found {len(matches)}')
    return matches[0]


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

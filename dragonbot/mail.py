import imaplib
import os
import re
import smtplib
import ssl
import time
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from .core import DEFAULT_CODE_REGEX, extract_code


class InboxConnection:
    def __init__(self):
        self.client = self.connect()
        try:
            self.uid_validity = self.validity(self.client)
        except Exception:
            self.discard(self.client)
            raise

    @staticmethod
    def discard(client):
        try:
            client.shutdown()
        except Exception:
            pass

    @classmethod
    def connect(cls):
        client = imaplib.IMAP4_SSL(os.environ.get('IMAP_HOST', 'imap.gmail.com'), timeout=30)
        try:
            client.login(os.environ['MAIL_USER'], os.environ['MAIL_PASSWORD'])
            status, _ = client.select('INBOX', readonly=True)
            if status != 'OK':
                raise RuntimeError('Cannot select verification inbox')
            return client
        except Exception:
            cls.discard(client)
            raise

    @staticmethod
    def validity(client):
        _, data = client.response('UIDVALIDITY')
        return data[0] if data and isinstance(data[0], (bytes, str)) else None

    def reconnect(self):
        self.discard(self.client)
        replacement = self.connect()
        try:
            valid = self.uid_validity is not None and self.validity(replacement) == self.uid_validity
        except Exception:
            self.discard(replacement)
            raise
        if not valid:
            self.discard(replacement)
            raise RuntimeError('Inbox UID validity changed; a fresh code request is required')
        self.client = replacement

    def __getattr__(self, name):
        return getattr(self.client, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        try:
            self.client.logout()
        except (imaplib.IMAP4.error, OSError):
            self.discard(self.client)


def inbox():
    return InboxConnection()


def highwater(client):
    status, data = client.uid('search', None, 'ALL')
    if status != 'OK':
        raise RuntimeError('Cannot read inbox UIDs')
    return max((int(x) for x in data[0].split()), default=0)


def wait_code(client, after, account):
    pattern = os.environ.get('MFA_CODE_REGEX') or DEFAULT_CODE_REGEX
    if re.compile(pattern).groups != 1:
        raise RuntimeError('MFA_CODE_REGEX must contain exactly one capture group')
    deadline = time.monotonic() + int(os.environ.get('MFA_TIMEOUT_SECONDS', '600'))
    reconnects = 0
    while time.monotonic() < deadline:
        try:
            client.noop()
            status, data = client.uid('search', None, f'UID {after + 1}:*')
            if status != 'OK':
                raise RuntimeError('Inbox search failed')
            for uid in data[0].split():
                if int(uid) <= after:
                    continue
                status, parts = client.uid('fetch', uid, '(BODY.PEEK[])')
                if status != 'OK':
                    raise RuntimeError('Inbox fetch failed')
                raw = next(item[1] for item in parts if isinstance(item, tuple))
                message = BytesParser(policy=policy.default).parsebytes(raw)
                code = extract_code(message, account, os.environ['MFA_ORIGINAL_SENDER'], pattern)
                if code:
                    return code
        except (imaplib.IMAP4.abort, ConnectionError, OSError):
            if reconnects >= 3 or time.monotonic() >= deadline:
                raise RuntimeError('Verification inbox connection dropped repeatedly') from None
            reconnects += 1
            print(f'Verification inbox disconnected; reconnecting ({reconnects}/3).', flush=True)
            client.reconnect()
            continue
        time.sleep(5)
    raise RuntimeError('Timed out waiting for a matching forwarded verification email')


def notify(subject, body, recipient=None):
    message = EmailMessage()
    message['From'] = os.environ['MAIL_USER']
    message['To'] = recipient or os.environ.get('NOTIFY_TO', 'rljr.code@gmail.com')
    message['Subject'] = subject
    message.set_content(body)
    with smtplib.SMTP(os.environ.get('SMTP_HOST', 'smtp.gmail.com'), 587, timeout=30) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(os.environ['MAIL_USER'], os.environ['MAIL_PASSWORD'])
        server.send_message(message)

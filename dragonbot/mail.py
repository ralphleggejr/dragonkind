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


def inbox():
    client = imaplib.IMAP4_SSL(os.environ.get('IMAP_HOST', 'imap.gmail.com'))
    client.login(os.environ['MAIL_USER'], os.environ['MAIL_PASSWORD'])
    client.select('INBOX', readonly=True)
    return client


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
    while time.monotonic() < deadline:
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

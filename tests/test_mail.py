from contextlib import redirect_stdout
import imaplib
import io
import os
import unittest
from unittest.mock import MagicMock, patch

from dragonbot.mail import inbox, wait_code

EMAIL = b'From: Dragonkind <hello@dragonkind.com>\nTo: one@gmail.com\nContent-Type: text/plain\n\nYour confirmation code is ABC123.'


def server(validity=b'123', abort=False):
    client = MagicMock()
    client.select.return_value = ('OK', [b'4'])
    client.response.return_value = ('UIDVALIDITY', [validity])
    if abort:
        client.noop.side_effect = imaplib.IMAP4.abort('fixture connection dropped')
    client.uid.side_effect = lambda command, *args: (
        ('OK', [b'41 43']) if command == 'search' else ('OK', [(b'43 (BODY[] ...)', EMAIL)]))
    return client


class MailReconnection(unittest.TestCase):
    def setUp(self):
        self.settings = patch.dict(os.environ, {'MAIL_USER': 'inbox@example.com', 'MAIL_PASSWORD': 'fixture-only',
            'MFA_ORIGINAL_SENDER': 'hello@dragonkind.com', 'MFA_TIMEOUT_SECONDS': '600'})
        self.settings.start()
    def tearDown(self):
        self.settings.stop()

    def test_imap_abort_reconnects_without_resetting_the_new_message_boundary(self):
        first, replacement = server(abort=True), server()
        with patch('dragonbot.mail.imaplib.IMAP4_SSL', side_effect=[first, replacement]) as connect, \
             redirect_stdout(io.StringIO()) as output:
            with inbox() as client:
                code = wait_code(client, 42, 'one@gmail.com')
            self.assertEqual(code, 'ABC123')
            self.assertEqual(connect.call_count, 2)
            replacement.uid.assert_any_call('search', None, 'UID 43:*')
            replacement.uid.assert_any_call('fetch', b'43', '(BODY.PEEK[])')
            self.assertFalse(any(call.args[:2] == ('fetch', b'41') for call in replacement.uid.call_args_list))
            first.shutdown.assert_called_once()
            replacement.logout.assert_called_once()
            replacement.select.assert_called_once_with('INBOX', readonly=True)
            self.assertNotIn('ABC123', output.getvalue())

    def test_uid_validity_change_requires_a_fresh_login_instead_of_accepting_old_mail(self):
        first, replacement = server(abort=True), server(validity=b'456')
        with patch('dragonbot.mail.imaplib.IMAP4_SSL', side_effect=[first, replacement]):
            with inbox() as client:
                with self.assertRaisesRegex(RuntimeError, 'UID validity changed'):
                    wait_code(client, 42, 'one@gmail.com')
            replacement.uid.assert_not_called()
            replacement.shutdown.assert_called_once()

    def test_reconnect_count_is_bounded_before_account_retry(self):
        clients = [server(abort=True) for _ in range(4)]
        with patch('dragonbot.mail.imaplib.IMAP4_SSL', side_effect=clients) as connect, \
             patch('dragonbot.mail.time.monotonic', return_value=1000):
            with inbox() as client:
                with self.assertRaisesRegex(RuntimeError, 'connection dropped repeatedly'):
                    wait_code(client, 42, 'one@gmail.com')
            self.assertEqual(connect.call_count, 4)

    def test_reconnecting_does_not_extend_the_original_mfa_deadline(self):
        first, replacement = server(abort=True), server()
        with patch.dict(os.environ, {'MFA_TIMEOUT_SECONDS': '2'}), \
             patch('dragonbot.mail.imaplib.IMAP4_SSL', side_effect=[first, replacement]), \
             patch('dragonbot.mail.time.monotonic', side_effect=[1000, 1000, 1000, 1003]):
            with inbox() as client:
                with self.assertRaisesRegex(RuntimeError, 'Timed out'):
                    wait_code(client, 42, 'one@gmail.com')
            replacement.uid.assert_not_called()

    def test_mail_logout_disconnect_does_not_discard_a_verified_code(self):
        client = server()
        client.logout.side_effect = imaplib.IMAP4.abort('fixture logout disconnect')
        with patch('dragonbot.mail.imaplib.IMAP4_SSL', return_value=client):
            with inbox() as connection:
                code = wait_code(connection, 42, 'one@gmail.com')
            self.assertEqual(code, 'ABC123')
            client.shutdown.assert_called_once()


if __name__ == '__main__':
    unittest.main()

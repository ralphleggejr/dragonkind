import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dragonbot.__main__ import (alert, deliver_tree_reports, queue_tree_report,
                               run_scheduler, TREE_REPORT_TO)
from dragonbot.core import load_state
from dragonbot.mail import notify


class TreeEmails(unittest.TestCase):
    def state(self):
        options = [{'text': 'Climb down slowly to pursue the sound', 'control': 'choice-0',
                    'painted': True, 'enabled': True},
                   {'text': 'Wait on the branch', 'control': 'choice-1',
                    'painted': True, 'enabled': True}]
        checks = [{'stage': stage, 'checked_at': '2026-10-28T12:00:00+00:00', 'option_count': 2,
                   'shadow_found': False, 'settled': True, 'options': options}
                  for stage in ('initial', 'before selection', 'before submission')]
        return {'accounts': {'one@gmail.com': {'status': 'cooldown', 'next_run': 20000,
                                              'tree_checkpoint_checks': checks}}, 'global_pause': False}

    def test_success_report_emails_all_three_checks_and_full_options(self):
        state = self.state()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch('dragonbot.__main__.STATE', path), patch('dragonbot.__main__.notify') as mail:
                queue_tree_report(state, 'one@gmail.com')
                mail.assert_called_once()
                self.assertEqual(mail.call_args.kwargs['recipient'], 'rljr.code@gmail.com')
                subject, body = mail.call_args.args
                self.assertIn('CONTINUED', subject)
                self.assertIn('Account: one@gmail.com', body)
                self.assertIn('pause if more than 2 options', body)
                for stage in ('initial', 'before selection', 'before submission'):
                    self.assertIn(stage, body)
                self.assertEqual(body.count('Options found: 2'), 3)
                self.assertEqual(body.count('Shadow found: NO'), 3)
                self.assertEqual(body.count('Wait on the branch'), 3)
                saved = load_state(path)
                self.assertFalse(saved['tree_report_outbox'])
                self.assertEqual(saved['accounts']['one@gmail.com']['last_tree_report']['status'], 'sent')
                self.assertEqual(saved['accounts']['one@gmail.com']['next_run'], 20000)

    def test_explicit_report_recipient_overrides_other_notification_setting(self):
        with patch.dict(os.environ, {'MAIL_USER': 'inbox@example.com', 'MAIL_PASSWORD': 'fixture-only',
                                     'NOTIFY_TO': 'another@example.com'}), patch('dragonbot.mail.smtplib.SMTP') as smtp:
            notify('Dragonkind tree check', 'Two options, no shadow.', recipient=TREE_REPORT_TO)
            message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
            self.assertEqual(message['To'], 'rljr.code@gmail.com')
            self.assertIn('Two options, no shadow.', message.get_content())

    def test_failed_clear_report_survives_restart_and_waits_before_retry(self):
        state = self.state()
        now = [1000]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch('dragonbot.__main__.STATE', path), \
                 patch('dragonbot.__main__.time.time', side_effect=lambda: now[0]), \
                 patch('dragonbot.__main__.notify', side_effect=[RuntimeError(), None]) as mail:
                queue_tree_report(state, 'one@gmail.com')
                saved = load_state(path)
                self.assertEqual(saved['tree_report_outbox'][0]['next_attempt'], 1300)
                self.assertEqual(saved['accounts']['one@gmail.com']['status'], 'cooldown')
                now[0] = 1299
                deliver_tree_reports(saved)
                self.assertEqual(mail.call_count, 1)
                now[0] = 1300
                deliver_tree_reports(saved)
                self.assertEqual(mail.call_count, 2)
                self.assertEqual(mail.call_args_list[0], mail.call_args_list[1])
                self.assertFalse(load_state(path)['tree_report_outbox'])
                self.assertFalse(saved['global_pause'])
                self.assertEqual(saved['accounts']['one@gmail.com']['next_run'], 20000)

    def test_no_duplicate_email_when_delivered_queue_is_flushed_again(self):
        state = self.state()
        with tempfile.TemporaryDirectory() as directory:
            with patch('dragonbot.__main__.STATE', Path(directory) / 'state.json'), \
                 patch('dragonbot.__main__.notify') as mail:
                queue_tree_report(state, 'one@gmail.com')
                deliver_tree_reports(state)
                self.assertEqual(mail.call_count, 1)

    def test_all_paused_scheduler_retries_pause_email_without_opening_browser(self):
        state = self.state()
        now = [1000]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch('dragonbot.__main__.STATE', path), \
                 patch('dragonbot.__main__.configuration', return_value={'accounts': ['one@gmail.com']}), \
                 patch('dragonbot.__main__.time.time', side_effect=lambda: now[0]), \
                 patch('dragonbot.__main__.time.sleep', side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds)), \
                 patch('playwright.sync_api.sync_playwright') as browser, \
                 patch('dragonbot.__main__.notify', side_effect=[RuntimeError(), None]) as mail:
                alert(state, 'one@gmail.com', 'A post-tree story option mentions shadow', global_pause=False,
                      details='Options found: 3\n1. Descend\n2. Wait\n3. Follow the shadow', recipient=TREE_REPORT_TO)
                run_scheduler()
                browser.assert_not_called()
                self.assertEqual(mail.call_count, 2)
                self.assertEqual(mail.call_args_list[0], mail.call_args_list[1])
                self.assertGreaterEqual(now[0], 1300)
                self.assertFalse(load_state(path)['accounts']['one@gmail.com']['notification_pending'])
                self.assertEqual(load_state(path)['accounts']['one@gmail.com']['status'], 'paused')

    def test_global_pause_still_delivers_queued_reports_without_running_accounts(self):
        state = self.state()
        now = [1000]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch('dragonbot.__main__.STATE', path), \
                 patch('dragonbot.__main__.configuration', return_value={'accounts': ['one@gmail.com']}), \
                 patch('dragonbot.__main__.time.time', side_effect=lambda: now[0]), \
                 patch('dragonbot.__main__.time.sleep', side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds)), \
                 patch('playwright.sync_api.sync_playwright') as browser, \
                 patch('dragonbot.__main__.notify', side_effect=[RuntimeError(), None]) as mail:
                queue_tree_report(state, 'one@gmail.com')
                state['accounts']['one@gmail.com'].update(status='paused', pause_scope='global')
                path.write_text(json.dumps(state))
                run_scheduler()
                browser.assert_not_called()
                self.assertEqual(mail.call_count, 2)
                saved = load_state(path)
                self.assertTrue(saved['global_pause'])
                self.assertFalse(saved['tree_report_outbox'])
                self.assertEqual(saved['accounts']['one@gmail.com']['status'], 'paused')


if __name__ == '__main__':
    unittest.main()

from imaplib import IMAP4
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dragonbot.__main__ import handle_account_failure, retry_pending_notifications, run_scheduler
from dragonbot.core import load_state
from dragonbot.retries import (clear_retry_after_success, protected_pause, retryable_failure, schedule_retry)
from dragonbot.rotation import begin_rotation, record_result
from dragonbot.runtime import account_deadline, next_attempt

REASON = 'Failure while waiting for the verification email (abort)'


class AutomaticRetries(unittest.TestCase):
    def test_backoff_is_persistent_and_capped_at_four_hours(self):
        record = {'next_run': 0}
        delays = []
        for _ in range(10):
            schedule_retry(record, REASON, 1000)
            delays.append(record['retry_at'] - 1000)
        self.assertEqual(delays, [300, 600, 1200, 2400, 4800, 9600, 14400, 14400, 14400, 14400])
        self.assertEqual(record['retry_attempts'], 10)
        self.assertEqual(record['next_run'], 0)
        self.assertEqual(len(record['retry_history']), 10)

    def test_retry_never_shortens_a_saved_site_cooldown(self):
        record = {'next_run': 20000}
        schedule_retry(record, REASON, 1000)
        self.assertEqual(record['next_run'], 20000)
        self.assertEqual(record['retry_at'], 20000)
        self.assertEqual(account_deadline(record), 20000)
        state = {'accounts': {'retry': record, 'other': {'next_run': 1400}}}
        self.assertEqual(next_attempt(state, ['retry', 'other'], 1000), ('other', 1400))

    def test_known_error_is_retryable_but_shadow_extra_options_and_manual_pauses_are_not(self):
        self.assertTrue(retryable_failure({'status': 'paused', 'reason': REASON}))
        for record in (
            {'status': 'paused', 'reason': 'A story option mentions shadow'},
            {'status': 'paused', 'reason': 'Tree checkpoint has 3 options; expected exactly 2'},
            {'status': 'paused', 'reason': REASON, 'tree_checkpoint_checks': [{'shadow_found': True, 'option_count': 2}]},
            {'status': 'paused', 'reason': REASON, 'tree_checkpoint_checks': [{'shadow_found': False, 'option_count': 3}]},
            {'status': 'paused', 'reason': 'Manual stop', 'pause_kind': 'manual'},
        ):
            self.assertTrue(protected_pause(record))
            self.assertFalse(retryable_failure(record))
        self.assertFalse(retryable_failure({'status': 'paused', 'reason': 'Unclassified manual review'}))

    def test_repeated_error_is_emailed_once_and_success_resets_backoff(self):
        state, now = {'accounts': {'a': {'next_run': 0}}, 'global_pause': False}, [1000]
        with tempfile.TemporaryDirectory() as directory:
            with patch('dragonbot.__main__.STATE', Path(directory) / 'state.json'), \
                 patch('dragonbot.__main__.time.time', side_effect=lambda: now[0]), \
                 patch('dragonbot.__main__.notify') as mail:
                handle_account_failure(state, 'a', REASON)
                now[0] = 1300
                handle_account_failure(state, 'a', REASON)
                self.assertEqual(mail.call_count, 1)
                record = state['accounts']['a']
                self.assertEqual(record['status'], 'retry_wait')
                self.assertEqual(record['retry_attempts'], 2)
                self.assertEqual(record['retry_at'], 1900)
                self.assertIn('Automatic retry:', mail.call_args.args[1])
                record.update(status='cooldown', next_run=20000)
                clear_retry_after_success(record)
                self.assertNotIn('retry_attempts', record)
                self.assertEqual(record['next_run'], 20000)
                now[0] = 20000
                handle_account_failure(state, 'a', REASON)
                self.assertEqual(mail.call_count, 2)
                self.assertEqual(record['retry_attempts'], 1)
                self.assertFalse(state['global_pause'])

    def test_retrying_a_failed_error_email_does_not_pause_or_reschedule_the_account(self):
        state, now = {'accounts': {'a': {'next_run': 0}}, 'global_pause': False}, [1000]
        with tempfile.TemporaryDirectory() as directory:
            with patch('dragonbot.__main__.STATE', Path(directory) / 'state.json'), \
                 patch('dragonbot.__main__.time.time', side_effect=lambda: now[0]), \
                 patch('dragonbot.__main__.notify', side_effect=[RuntimeError(), None]) as mail:
                handle_account_failure(state, 'a', REASON)
                now[0] = 1300
                retry_pending_notifications(state, ['a'])
                self.assertEqual(mail.call_count, 2)
                self.assertEqual(mail.call_args_list[0], mail.call_args_list[1])
                self.assertEqual(state['accounts']['a']['status'], 'retry_wait')
                self.assertEqual(state['accounts']['a']['retry_attempts'], 1)
                self.assertEqual(state['accounts']['a']['retry_at'], 1300)

    def test_old_global_mail_abort_resumes_other_accounts_and_retries_without_reset(self):
        state = {'accounts': {
            'finished': {'status': 'cooldown', 'next_run': 20000},
            'failed': {'status': 'paused', 'reason': REASON, 'pause_scope': 'global', 'next_run': 0},
            'remaining': {'status': 'pending', 'next_run': 0}}, 'global_pause': True}
        now, played = [1000], []
        def attempt(engine, route, saved, account, headless):
            played.append((account, now[0]))
            saved['accounts'][account].update(status='cooldown', next_run=now[0] + 14400)
            clear_retry_after_success(saved['accounts'][account])
        def wait(seconds):
            if len(played) == 2:
                raise StopIteration
            now[0] += seconds
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            path.write_text(json.dumps(state))
            with patch('dragonbot.__main__.STATE', path), \
                 patch('dragonbot.__main__.configuration', return_value={'accounts': list(state['accounts'])}), \
                 patch('dragonbot.__main__.time.time', side_effect=lambda: now[0]), \
                 patch('dragonbot.__main__.time.sleep', side_effect=wait), \
                 patch('playwright.sync_api.sync_playwright'), \
                 patch('dragonbot.__main__.run_account', side_effect=attempt), patch('dragonbot.__main__.notify'):
                with self.assertRaises(StopIteration):
                    run_scheduler()
                saved = load_state(path)
                self.assertEqual(played, [('remaining', 1000), ('failed', 1300)])
                self.assertEqual(saved['accounts']['finished']['next_run'], 20000)
                self.assertEqual(saved['accounts']['failed']['next_run'], 15700)
                self.assertNotIn('retry_attempts', saved['accounts']['failed'])
                self.assertFalse(saved['global_pause'])

    def test_failed_account_does_not_block_the_rest_and_summary_includes_pending_retry(self):
        state = {'accounts': {'failed': {'next_run': 0}, 'remaining': {'next_run': 0}}, 'global_pause': False}
        now, played = [1000], []
        def attempt(engine, route, saved, account, headless):
            played.append((account, now[0]))
            if len(played) == 1:
                raise IMAP4.abort('fixture disconnect')
            saved['accounts'][account].update(status='cooldown', next_run=now[0] + 14400)
            clear_retry_after_success(saved['accounts'][account])
        def wait(seconds):
            if len(played) == 3:
                raise StopIteration
            now[0] += seconds
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            path.write_text(json.dumps(state))
            with patch('dragonbot.__main__.STATE', path), \
                 patch('dragonbot.__main__.configuration', return_value={'accounts': list(state['accounts'])}), \
                 patch('dragonbot.__main__.time.time', side_effect=lambda: now[0]), \
                 patch('dragonbot.__main__.time.sleep', side_effect=wait), \
                 patch('playwright.sync_api.sync_playwright'), \
                 patch('dragonbot.__main__.run_account', side_effect=attempt), \
                 patch('dragonbot.__main__.notify') as mail:
                with self.assertRaises(StopIteration):
                    run_scheduler()
                self.assertEqual(played, [('failed', 1000), ('remaining', 1000), ('failed', 1300)])
                self.assertEqual(mail.call_count, 2)
                self.assertIn('error; retry scheduled', mail.call_args_list[0].args[0])
                self.assertIn('COMPLETE WITH RETRIES', mail.call_args_list[1].args[0])
                self.assertIn('Accounts awaiting automatic retry: 1', mail.call_args_list[1].args[1])

    def test_failed_attempt_is_not_reported_twice(self):
        state = {'accounts': {'a': {'status': 'active'}}, 'global_pause': False}
        begin_rotation(state, ['a'])['in_progress'] = 'a'
        state['accounts']['a'].update(status='paused', reason=REASON)
        record_result(state, ['a'], 'a')
        self.assertEqual(len(state['reporting_rotation']['results']['a']), 1)
        self.assertTrue(state['reporting_rotation']['results']['a'][0]['attempted'])


if __name__ == '__main__':
    unittest.main()

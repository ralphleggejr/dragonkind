from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from dragonbot.__main__ import (alert, deliver_tree_reports, finish_rotation_report,
    migrate_individual_reports, run_scheduler, TREE_REPORT_TO)
from dragonbot.core import load_state
from dragonbot.mail import notify
from dragonbot.rotation import begin_rotation, record_result, recover_finished_result

ACCOUNTS = json.loads(Path('route.json').read_text())['accounts']


class RotationEmails(unittest.TestCase):
    def state(self):
        options = [{'text': 'Climb down slowly to pursue the sound', 'control': 'choice-0',
                    'painted': True, 'enabled': True},
                   {'text': 'Wait on the branch', 'control': 'choice-1', 'painted': True, 'enabled': True}]
        checks = [{'stage': stage, 'checked_at': '2026-10-28T12:00:00+00:00', 'option_count': 2,
                   'shadow_found': False, 'settled': True, 'options': deepcopy(options)}
                  for stage in ('initial', 'before selection', 'before submission')]
        return {'accounts': {account: {'status': 'cooldown', 'next_run': 20000,
                'tree_checkpoint_checks': deepcopy(checks)} for account in ACCOUNTS}, 'global_pause': False}

    def complete_round(self, state):
        begin_rotation(state, ACCOUNTS)
        for account in ACCOUNTS:
            record_result(state, ACCOUNTS, account)
        finish_rotation_report(state)

    def test_one_summary_after_all_eight_accounts_with_all_checks_and_timers(self):
        state = self.state()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch('dragonbot.__main__.STATE', path), patch('dragonbot.__main__.notify') as mail:
                begin_rotation(state, ACCOUNTS)
                for account in ACCOUNTS[:-1]:
                    record_result(state, ACCOUNTS, account)
                    finish_rotation_report(state)
                    deliver_tree_reports(state)
                    mail.assert_not_called()
                record_result(state, ACCOUNTS, ACCOUNTS[-1])
                finish_rotation_report(state)
                deliver_tree_reports(state)
                mail.assert_called_once()
                self.assertEqual(mail.call_args.kwargs['recipient'], TREE_REPORT_TO)
                subject, body = mail.call_args.args
                self.assertIn('COMPLETE: 8/8', subject)
                for account in ACCOUNTS:
                    self.assertIn(account, body)
                for text in ('Options found: 2', 'Shadow found: NO', 'All options visible: YES', 'Animations complete: YES'):
                    self.assertEqual(body.count(text), 24)
                self.assertEqual(body.count('Next eligible:'), 8)
                saved = load_state(path)
                self.assertFalse(saved['tree_report_outbox'])
                self.assertEqual(saved['last_rotation_report']['status'], 'sent')
                self.assertTrue(all(record['next_run'] == 20000 for record in saved['accounts'].values()))

    def simulate(self, staggered=False, shadow=False, failure=False, all_paused=False, rounds=1):
        state = self.state()
        now, played, events = [1000], [], []
        for index, record in enumerate(state['accounts'].values()):
            record['next_run'] = 1000 + (index * 10 if staggered else 0)
        driver = MagicMock()
        driver.__enter__.side_effect = lambda: events.append('driver-open') or MagicMock()
        driver.__exit__.side_effect = lambda *args: events.append('driver-closed')
        def play(engine, route, saved, account, headless):
            summaries = [call for call in mail.call_args_list if 'Dragonkind rotation' in call.args[0]]
            self.assertEqual(len(summaries), len(played) // 8)
            played.append((account, now[0]))
            if failure:
                alert(saved, account, 'Login failure (TimeoutError)')
                mail.assert_not_called()
            elif all_paused:
                alert(saved, account, 'Tree checkpoint has 3 options; expected exactly 2', global_pause=False)
            elif shadow and account == ACCOUNTS[2]:
                checks = saved['accounts'][account]['tree_checkpoint_checks']
                checks[-1].update(shadow_found=True, option_count=3)
                checks[-1]['options'].append({'text': 'Follow the shadow', 'painted': True})
                alert(saved, account, 'A story option mentions shadow', global_pause=False,
                      details='Options found: 3\nFollow the shadow', recipient=TREE_REPORT_TO)
                self.assertEqual(mail.call_count, 1)
                self.assertEqual(len(played), 3)
            else:
                saved['accounts'][account].update(status='cooldown', next_run=now[0] + 14400)
        def wait(seconds):
            if sum('Dragonkind rotation' in call.args[0] for call in mail.call_args_list) >= rounds:
                raise StopIteration
            now[0] += seconds
            self.assertLess(now[0], 100000, 'Summary never completed')
        def sent(subject, body, **kwargs):
            if subject.startswith('Dragonkind rotation'):
                self.assertEqual(events[-1], 'driver-closed')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            path.write_text(json.dumps(state))
            with patch('dragonbot.__main__.STATE', path), \
                 patch('dragonbot.__main__.configuration', return_value={'accounts': ACCOUNTS}), \
                 patch('dragonbot.__main__.time.time', side_effect=lambda: now[0]), \
                 patch('dragonbot.__main__.time.sleep', side_effect=wait), \
                 patch('playwright.sync_api.sync_playwright', return_value=driver), \
                 patch('dragonbot.__main__.run_account', side_effect=play), \
                 patch('dragonbot.__main__.notify', side_effect=sent) as mail:
                try:
                    run_scheduler()
                except StopIteration:
                    pass
                return load_state(path), mail, played

    def test_staggered_deadlines_produce_one_email_and_keep_independent_cooldowns(self):
        saved, mail, played = self.simulate(staggered=True)
        self.assertEqual(mail.call_count, 1)
        self.assertEqual(played, [(account, 1000 + index * 10) for index, account in enumerate(ACCOUNTS)])
        self.assertNotIn('reporting_rotation', saved)
        self.assertTrue(all(saved['accounts'][account]['next_run'] == start + 14400 for account, start in played))

    def test_shadow_emails_immediately_then_one_summary_after_remaining_accounts(self):
        saved, mail, played = self.simulate(shadow=True)
        self.assertEqual([account for account, _ in played], ACCOUNTS)
        self.assertEqual(mail.call_count, 2)
        self.assertIn('paused', mail.call_args_list[0].args[0])
        self.assertIn('COMPLETE: 8/8', mail.call_args_list[1].args[0])
        self.assertIn('Follow the shadow', mail.call_args_list[1].args[1])
        self.assertFalse(saved['global_pause'])

    def test_each_four_hour_rotation_sends_exactly_one_summary(self):
        saved, mail, played = self.simulate(staggered=True, rounds=2)
        self.assertEqual(mail.call_count, 2)
        self.assertEqual([account for account, _ in played], ACCOUNTS * 2)
        self.assertEqual([start for _, start in played[8:]], [start + 14400 for _, start in played[:8]])

    def test_all_nonshadow_pauses_produce_one_summary_not_two(self):
        saved, mail, played = self.simulate(staggered=True, all_paused=True)
        self.assertEqual(mail.call_count, 1)
        self.assertEqual(len(played), 8)
        self.assertTrue(all(record['status'] == 'paused' for record in saved['accounts'].values()))

    def test_global_failure_ends_round_with_one_partial_summary(self):
        saved, mail, played = self.simulate(failure=True)
        self.assertEqual(len(played), 1)
        self.assertEqual(mail.call_count, 1)
        self.assertIn('STOPPED EARLY: 1/8', mail.call_args.args[0])
        self.assertEqual(mail.call_args.args[1].count('NOT ATTEMPTED:'), 7)
        self.assertTrue(saved['global_pause'])

    def test_partial_round_survives_restart_and_check_snapshots_are_preserved(self):
        state = self.state()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch('dragonbot.__main__.STATE', path), patch('dragonbot.__main__.notify') as mail:
                begin_rotation(state, ACCOUNTS)
                for account in ACCOUNTS[:4]:
                    record_result(state, ACCOUNTS, account)
                path.write_text(json.dumps(state))
                saved = load_state(path)
                saved['accounts'][ACCOUNTS[0]]['tree_checkpoint_checks'] = []
                begin_rotation(saved, ACCOUNTS)
                finish_rotation_report(saved)
                self.assertNotIn('tree_report_outbox', saved)
                for account in ACCOUNTS[4:]:
                    record_result(saved, ACCOUNTS, account)
                finish_rotation_report(saved)
                deliver_tree_reports(saved)
                self.assertEqual(mail.call_count, 1)
                self.assertEqual(mail.call_args.args[1].count('Options found: 2'), 24)

    def test_nonshadow_pause_is_deferred_and_paused_account_is_in_summary(self):
        state = self.state()
        with tempfile.TemporaryDirectory() as directory:
            with patch('dragonbot.__main__.STATE', Path(directory) / 'state.json'), patch('dragonbot.__main__.notify') as mail:
                alert(state, ACCOUNTS[0], 'Tree checkpoint has 3 options; expected exactly 2', global_pause=False)
                mail.assert_not_called()
                self.assertTrue(state['accounts'][ACCOUNTS[0]]['notification_deferred'])
                begin_rotation(state, ACCOUNTS)
                for account in ACCOUNTS[1:]:
                    record_result(state, ACCOUNTS, account)
                finish_rotation_report(state)
                deliver_tree_reports(state)
                mail.assert_called_once()
                self.assertIn('has 3 options', mail.call_args.args[1])
                self.assertFalse(state['global_pause'])

    def test_restart_after_last_status_save_recovers_the_completed_attempt_once(self):
        state = self.state()
        begin_rotation(state, ACCOUNTS)['in_progress'] = ACCOUNTS[0]
        recover_finished_result(state, ACCOUNTS)
        recover_finished_result(state, ACCOUNTS)
        results = state['reporting_rotation']['results'][ACCOUNTS[0]]
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]['attempted'])
        self.assertEqual(results[0]['next_run'], 20000)

    def test_failed_summary_survives_restart_and_retry_does_not_duplicate_reports(self):
        state, now = self.state(), [1000]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch('dragonbot.__main__.STATE', path), \
                 patch('dragonbot.__main__.time.time', side_effect=lambda: now[0]), \
                 patch('dragonbot.__main__.notify', side_effect=[RuntimeError(), None]) as mail:
                self.complete_round(state)
                deliver_tree_reports(state)
                saved = load_state(path)
                self.assertEqual(saved['tree_report_outbox'][0]['next_attempt'], 1300)
                now[0] = 1299
                deliver_tree_reports(saved)
                self.assertEqual(mail.call_count, 1)
                now[0] = 1300
                deliver_tree_reports(saved)
                deliver_tree_reports(saved)
                self.assertEqual(mail.call_count, 2)
                self.assertEqual(mail.call_args_list[0], mail.call_args_list[1])
                self.assertFalse(load_state(path)['tree_report_outbox'])
                self.assertTrue(all(record['next_run'] == 20000 for record in saved['accounts'].values()))

    def test_old_individual_report_is_in_summary_without_a_separate_email(self):
        state = self.state()
        state['tree_report_outbox'] = [{'body': 'Previously unsent tree checks', 'account': ACCOUNTS[0]}]
        with tempfile.TemporaryDirectory() as directory:
            with patch('dragonbot.__main__.STATE', Path(directory) / 'state.json'), patch('dragonbot.__main__.notify') as mail:
                migrate_individual_reports(state)
                deliver_tree_reports(state)
                mail.assert_not_called()
                self.complete_round(state)
                deliver_tree_reports(state)
                mail.assert_called_once()
                self.assertIn('Previously unsent tree checks', mail.call_args.args[1])

    def test_recipient_overrides_other_notification_setting(self):
        with patch.dict(os.environ, {'MAIL_USER': 'inbox@example.com', 'MAIL_PASSWORD': 'fixture-only',
                                     'NOTIFY_TO': 'another@example.com'}), patch('dragonbot.mail.smtplib.SMTP') as smtp:
            notify('Dragonkind rotation', 'All accounts handled.', recipient=TREE_REPORT_TO)
            message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
            self.assertEqual(message['To'], 'rljr.code@gmail.com')

    def test_all_paused_scheduler_retries_shadow_email_without_opening_browser(self):
        state, now = self.state(), [1000]
        state['accounts'] = {ACCOUNTS[0]: state['accounts'][ACCOUNTS[0]]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch('dragonbot.__main__.STATE', path), \
                 patch('dragonbot.__main__.configuration', return_value={'accounts': [ACCOUNTS[0]]}), \
                 patch('dragonbot.__main__.time.time', side_effect=lambda: now[0]), \
                 patch('dragonbot.__main__.time.sleep', side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds)), \
                 patch('playwright.sync_api.sync_playwright') as browser, \
                 patch('dragonbot.__main__.notify', side_effect=[RuntimeError(), None]) as mail:
                alert(state, ACCOUNTS[0], 'A story option mentions shadow', global_pause=False,
                      details='Options found: 3\nFollow the shadow', recipient=TREE_REPORT_TO)
                run_scheduler()
                browser.assert_not_called()
                self.assertEqual(mail.call_count, 2)
                self.assertEqual(mail.call_args_list[0], mail.call_args_list[1])
                self.assertFalse(load_state(path)['accounts'][ACCOUNTS[0]]['notification_pending'])


if __name__ == '__main__':
    unittest.main()

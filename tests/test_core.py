import json
import os
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch
from dragonbot.core import DEFAULT_CODE_REGEX, choose, extract_code, load_state, save_state, shadow, login_request_summary, refresh_global_pause, retry_login_rejections, locate_story_step, resume_story_failures


class Decisions(unittest.TestCase):
    def test_exact_and_ambiguous_choices(self):
        self.assertEqual(choose(['Continue', 'Exit'], ['continue']), 0)
        with self.assertRaises(RuntimeError):
            choose(['continue', 'Continue'], ['continue'])
        with self.assertRaises(RuntimeError):
            choose(['Continue into danger'], ['continue'])

    def test_branch_and_typography(self):
        self.assertEqual(choose(['Lower your eyes in respect'],
                                ['Lower your eyes and hold still', 'Lower your eyes in respect']), 0)
        self.assertEqual(choose(['Jump now and you can land on it’s back.'],
                                ["jump now and you can land on it's back"]), 0)

    def test_confirmed_dragon_branches_and_tree_label(self):
        route = json.loads(Path('route.json').read_text())
        branches = route['steps'][6]
        for label in ('Lower your eyes and turn to face it', 'Lower your eyes in respect'):
            self.assertEqual(choose(['Run away', 'Attack', label], branches), 2)
        brown = ['RUN AND HOPE IT DIDN’T SEE YOU', 'LOWER YOUR EYES AND HOLD STILL',
                 'LOWER YOUR EYES AND TURN TO FACE IT']
        self.assertEqual(choose(brown, branches), 2)
        self.assertEqual(locate_story_step(brown, route['steps']), 6)
        with self.assertRaises(RuntimeError):
            choose(['Lower your eyes and hold still'], branches)
        self.assertEqual(route['steps'][7], ['Head deeper into the forest'])
        self.assertEqual(choose(['Wait', 'Climb down slowly to pursue the sound'],
                                route['steps'][route['shadow_checkpoint']]), 1)

    def test_recorded_prefix_requires_one_match_and_a_word_boundary(self):
        prefix = [{'prefix': 'Lower your eyes but keep your'}]
        self.assertEqual(choose(['Run', 'Lower your eyes but keep your head high'], prefix), 1)
        with self.assertRaises(RuntimeError):
            choose(['Lower your eyes but keep your head high', 'Lower your eyes but keep your hands still'], prefix)
        with self.assertRaises(RuntimeError):
            choose(['Lower your eyes but keep yourself safe'], prefix)
        with self.assertRaises(RuntimeError):
            choose(['Run'], [{'prefix': ''}])

    def test_resume_position_requires_a_unique_recorded_choice(self):
        steps = json.loads(Path('route.json').read_text())['steps']
        self.assertEqual(locate_story_step(['Lower your eyes and turn to face it', 'Run'], steps), 6)
        self.assertEqual(locate_story_step(['Climb down slowly to pursue the sound', 'Wait'], steps), 18)
        for labels in (['Continue'], ['An unknown choice'],
                       ['Help your squadmate', 'Investigate the scream'],
                       ['Lower your eyes and turn to face it', 'LOWER YOUR EYES AND TURN TO FACE IT']):
            with self.subTest(labels=labels), self.assertRaises(RuntimeError):
                locate_story_step(labels, steps)

    def test_shadow_in_any_option(self):
        self.assertTrue(shadow(['a', 'b', 'A shadow moves']))
        self.assertTrue(shadow(['shadow', 'b', 'c']))
        self.assertTrue(shadow(['a', 'A SHADOW moves']))
        self.assertFalse(shadow([]))
        self.assertFalse(shadow(['a', 'b']))

    def test_hope_requires_unique_match(self):
        self.assertEqual(choose(['Run', 'Close your eyes. Hope for the best!'],
                                ['Hope for the best'], True), 1)

    def test_atomic_state_retains_cooldown_and_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            state = {'accounts': {'a': {'next_run': 14400, 'status': 'paused'}}, 'global_pause': False}
            save_state(path, state)
            self.assertEqual(load_state(path), state)

    def message(self, body):
        return BytesParser(policy=policy.default).parsebytes(
            ('From: owner@gmail.com\nTo: inbox@gmail.com\nContent-Type: text/plain\n\n' + body).encode())

    def test_forwarded_code_matches_original_account_and_sender(self):
        msg = self.message('From: login@dragonkind.com\nTo: one@gmail.com\nCode: 123456')
        self.assertEqual(extract_code(msg, 'one@gmail.com', 'login@dragonkind.com', r'\b([0-9]{6})\b'), '123456')
        self.assertIsNone(extract_code(msg, 'two@gmail.com', 'login@dragonkind.com', r'\b([0-9]{6})\b'))
        self.assertIsNone(extract_code(msg, 'one@gmail.com', 'other@dragonkind.com', r'\b([0-9]{6})\b'))

    def test_forward_destination_is_not_original_account(self):
        msg = self.message('From: login@dragonkind.com\nTo: one@gmail.com\nCode: 123456')
        self.assertIsNone(extract_code(msg, 'inbox@gmail.com', 'login@dragonkind.com', r'\b([0-9]{6})\b'))

    def test_direct_verification_email(self):
        msg = BytesParser(policy=policy.default).parsebytes(
            b'From: login@dragonkind.com\nTo: inbox@gmail.com\nContent-Type: text/plain\n\nCode: 123456')
        self.assertEqual(extract_code(msg, 'inbox@gmail.com', 'login@dragonkind.com', r'\b([0-9]{6})\b'), '123456')

    def test_ambiguous_code_is_rejected(self):
        msg = self.message('From: login@dragonkind.com\nTo: one@gmail.com\nCodes: 123456 and 654321')
        self.assertIsNone(extract_code(msg, 'one@gmail.com', 'login@dragonkind.com', r'\b([0-9]{6})\b'))

    def test_real_email_wording_and_alphanumeric_format(self):
        for code in ('A1B-2C3', 'ABC123', '123456'):
            with self.subTest(code=code):
                msg = self.message('From: Dragonkind <hello@dragonkind.com>\nTo: one@gmail.com\nYour confirmation code is ' + code + '.')
                self.assertEqual(extract_code(msg, 'one@gmail.com', 'hello@dragonkind.com', DEFAULT_CODE_REGEX), code)

    def test_default_pattern_rejects_words_and_invalid_lengths(self):
        for body in ('Please confirm your account.', 'Your confirmation code is ABC1234.',
                     'Your confirmation code is AB-1234.', 'Your confirmation code is ABC-123-X.'):
            msg = self.message('From: hello@dragonkind.com\nTo: one@gmail.com\n' + body)
            self.assertIsNone(extract_code(msg, 'one@gmail.com', 'hello@dragonkind.com', DEFAULT_CODE_REGEX))

    def test_login_request_diagnostics_never_expose_token_or_cookie_values(self):
        result = login_request_summary('reader=leggejr.co%40gmail.com&_csrf_token=SECRET_TOKEN&other=SECRET_OTHER',
                                       {'Cookie': 'session=SECRET_COOKIE', 'Origin': 'https://dragonkind.com',
                                        'Referer': 'https://dragonkind.com'}, 'leggejr.co@gmail.com')
        self.assertEqual(result['reader_values'], ['leggejr.co@gmail.com'])
        self.assertTrue(result['reader_matches_account_exactly'])
        self.assertTrue(result['csrf_field_nonempty'])
        self.assertTrue(result['cookie_header_present'])
        self.assertNotIn('SECRET', json.dumps(result))

    def test_inactive_account_pause_does_not_block_selected_new_account(self):
        state = {'accounts': {
            'old@gmail.com': {'status': 'paused', 'reason': 'HTTP 422', 'next_run': 0},
            'new@gmail.com': {'next_run': 0}}, 'global_pause': True}
        refresh_global_pause(state, ['new@gmail.com'])
        self.assertFalse(state['global_pause'])
        self.assertEqual(state['accounts']['old@gmail.com']['status'], 'paused')
        self.assertEqual(state['accounts']['old@gmail.com']['reason'], 'HTTP 422')
        refresh_global_pause(state, ['old@gmail.com'])
        self.assertTrue(state['global_pause'])

    def test_account_only_shadow_pause_stays_account_only(self):
        state = {'accounts': {'shadow@gmail.com': {'status': 'paused'},
                              'new@gmail.com': {'next_run': 9000}}, 'global_pause': False}
        refresh_global_pause(state, ['shadow@gmail.com', 'new@gmail.com'])
        self.assertFalse(state['global_pause'])
        self.assertEqual(state['accounts']['shadow@gmail.com']['pause_scope'], 'account')
        self.assertEqual(state['accounts']['new@gmail.com']['next_run'], 9000)

    def test_another_configured_global_pause_still_blocks(self):
        state = {'accounts': {'old@gmail.com': {'status': 'paused', 'pause_scope': 'global'},
                              'new@gmail.com': {'next_run': 0}}, 'global_pause': True}
        refresh_global_pause(state, ['old@gmail.com', 'new@gmail.com'])
        self.assertTrue(state['global_pause'])

    def test_reset_selected_account_ignores_inactive_pause_and_retains_cooldown(self):
        from dragonbot.__main__ import main
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                Path('route.json').write_text(json.dumps({'accounts': ['new@gmail.com']}))
                state_path = Path(directory) / 'state.json'
                state = {'accounts': {'old@gmail.com': {'status': 'paused', 'reason': 'HTTP 422'},
                                      'new@gmail.com': {'next_run': 9000}}, 'global_pause': True}
                save_state(state_path, state)
                with patch('dragonbot.__main__.STATE', state_path), \
                     patch('sys.argv', ['dragonbot', 'reset', '--account', 'new@gmail.com', '--confirm-site-reset']):
                    main()
                result = load_state(state_path)
                self.assertFalse(result['global_pause'])
                self.assertEqual(result['accounts']['old@gmail.com']['status'], 'paused')
                self.assertEqual(result['accounts']['new@gmail.com']['next_run'], 9000)
            finally:
                os.chdir(previous)

    def test_account_runs_close_browser_before_the_next_run_starts(self):
        from dragonbot.__main__ import run_account
        from unittest.mock import MagicMock
        events = []
        engine = MagicMock()
        browsers = [MagicMock(), MagicMock()]
        for index, browser in enumerate(browsers):
            browser.close.side_effect = lambda i=index: events.append(f'close-{i}')
        def launch(**kwargs):
            index = sum(event.startswith('launch-') for event in events)
            events.append(f'launch-{index}')
            return browsers[index]
        engine.chromium.launch.side_effect = launch
        with patch('dragonbot.__main__.play', side_effect=lambda *args, **kwargs: events.append('play')):
            run_account(engine, {}, {}, 'one@gmail.com')
            run_account(engine, {}, {}, 'two@gmail.com')
        self.assertEqual(events, ['launch-0', 'play', 'close-0', 'launch-1', 'play', 'close-1'])

    def test_retry_browser_is_closed_before_replacement_and_closed_on_failure(self):
        from dragonbot.__main__ import run_account
        from unittest.mock import MagicMock
        events = []
        engine = MagicMock()
        first, second = MagicMock(), MagicMock()
        first.close.side_effect = lambda: events.append('close-first')
        second.close.side_effect = lambda: events.append('close-second')
        def launch(**kwargs):
            if not events:
                events.append('launch-first')
                return first
            events.append('launch-second')
            return second
        engine.chromium.launch.side_effect = launch
        def failing_run(*args, restart_browser):
            self.assertIs(restart_browser(), second)
            raise RuntimeError('fixture failure')
        with patch('dragonbot.__main__.play', side_effect=failing_run), self.assertRaises(RuntimeError):
            run_account(engine, {}, {}, 'one@gmail.com')
        self.assertEqual(events, ['launch-first', 'close-first', 'launch-second', 'close-second'])

    def test_manual_browser_closure_reports_cancel_without_traceback(self):
        from dragonbot.__main__ import replay_login
        from playwright.sync_api import Error as BrowserError
        from unittest.mock import MagicMock
        engine = MagicMock()
        browser = engine.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.is_closed.return_value = True
        with patch.dict(os.environ, {}), \
             patch('playwright.sync_api.sync_playwright') as driver, \
             patch('dragonbot.__main__.replay_login_page', side_effect=BrowserError('fixture window closed')):
            driver.return_value.__enter__.return_value = engine
            self.assertFalse(replay_login('ralphleggejr@gmail.com'))
            context.close.assert_called_once()
            browser.close.assert_called_once()

    def test_active_accounts_exclude_temporarily_removed_address(self):
        accounts = json.loads(Path('route.json').read_text())['accounts']
        self.assertEqual(accounts, ['ralphleggejr.apple@gmail.com',
            'leggetechsolutions@gmail.com', 'leggefamilyco@gmail.com', 'rljr.code@gmail.com',
            'rlegge.code@gmail.com', 'leggejr.co@gmail.com', '10249southlawncir@gmail.com'])

    def test_retry_login_errors_keeps_cooldowns_and_other_pauses(self):
        state = {'accounts': {
            'rejected@gmail.com': {'status': 'paused', 'step': 0, 'next_run': 9000,
                'reason': 'Failure while submitting the login email (LoginRequestRejected; HTTP 422)'},
            'shadow@gmail.com': {'status': 'paused', 'step': 18, 'reason': 'shadow', 'pause_scope': 'account'},
            'unknown@gmail.com': {'status': 'paused', 'step': 0, 'reason': 'TimeoutError', 'pause_scope': 'global'}},
            'global_pause': True}
        accounts = list(state['accounts'])
        self.assertEqual(retry_login_rejections(state, accounts), ['rejected@gmail.com'])
        self.assertEqual(state['accounts']['rejected@gmail.com']['next_run'], 9000)
        self.assertEqual(state['accounts']['rejected@gmail.com']['status'], 'pending')
        self.assertEqual(state['accounts']['shadow@gmail.com']['status'], 'paused')
        self.assertTrue(state['global_pause'])

    def test_resume_stories_retains_shadow_mail_errors_inactive_accounts_and_cooldowns(self):
        state = {'accounts': {
            'story@gmail.com': {'status': 'paused', 'step': 6, 'next_run': 9000,
                'reason': 'Failure while following the story route (RuntimeError)', 'pause_scope': 'global'},
            'shadow@gmail.com': {'status': 'paused', 'step': 18, 'reason': 'A story option mentions shadow', 'pause_scope': 'account'},
            'mail@gmail.com': {'status': 'paused', 'reason': 'Failure while waiting for the verification email (TimeoutError)', 'pause_scope': 'global'},
            'excluded@gmail.com': {'status': 'paused', 'reason': 'Failure while following the story route (RuntimeError)', 'pause_scope': 'global'}},
            'global_pause': True}
        self.assertEqual(resume_story_failures(state, ['story@gmail.com', 'shadow@gmail.com', 'mail@gmail.com']), ['story@gmail.com'])
        record = state['accounts']['story@gmail.com']
        self.assertEqual(record['status'], 'pending')
        self.assertEqual(record['next_run'], 9000)
        self.assertIn('previous_story_failure', record)
        for account in ('shadow@gmail.com', 'mail@gmail.com', 'excluded@gmail.com'):
            self.assertEqual(state['accounts'][account]['status'], 'paused')
        self.assertTrue(state['global_pause'])

    def test_desktop_scheduler_resumes_brown_failure_and_retains_cooldown_and_shadow_pause(self):
        from dragonbot.__main__ import run
        from unittest.mock import MagicMock
        route = json.loads(Path('route.json').read_text())
        accounts = route['accounts']
        excluded = 'ralphleggejr@gmail.com'
        state = {'accounts': {accounts[0]: {'status': 'cooldown', 'next_run': 2000},
                              accounts[1]: {'status': 'paused', 'pause_scope': 'global',
                                            'reason': 'Failure while inspecting the resumed story (StoryPositionError; Multiple options match a recorded story step)'},
                              accounts[2]: {'status': 'paused', 'pause_scope': 'account',
                                            'reason': 'A story option mentions shadow'},
                              excluded: {'status': 'paused', 'step': 0, 'next_run': 0,
                                         'reason': 'Failure while completing login (TimeoutError)',
                                         'pause_scope': 'global'}}, 'global_pause': True}
        played = []
        def attempt(engine, plan, saved, account, headless):
            self.assertFalse(headless)
            played.append(account)
            saved['accounts'][account] = {'status': 'cooldown', 'next_run': 15400}
        with tempfile.TemporaryDirectory() as directory:
            with patch('dragonbot.__main__.configuration', return_value=route), \
                 patch('dragonbot.__main__.load_state', return_value=state), \
                 patch('dragonbot.__main__.STATE', Path(directory) / 'state.json'), \
                 patch('playwright.sync_api.sync_playwright', return_value=MagicMock()), \
                 patch('dragonbot.__main__.time.time', return_value=1000), \
                 patch('dragonbot.__main__.time.sleep', side_effect=StopIteration), \
                 patch('dragonbot.__main__.run_account', side_effect=attempt):
                with self.assertRaises(StopIteration):
                    run(headless=False, resume_stories=True)
        self.assertEqual(played, [accounts[1]] + accounts[3:])
        self.assertEqual(state['accounts'][accounts[0]]['next_run'], 2000)
        self.assertEqual(state['accounts'][accounts[2]]['status'], 'paused')
        self.assertFalse(state['global_pause'])
        self.assertEqual(state['accounts'][excluded]['status'], 'paused')

    def test_route_configuration(self):
        route = json.loads(Path('route.json').read_text())
        self.assertEqual(len(set(route['accounts'])), 7)
        self.assertEqual(route['cooldown_seconds'], 14400)
        self.assertEqual(route['steps'][route['shadow_checkpoint']],
                         [{'prefix': 'Climb down slowly to pursue'}])
        self.assertEqual(route['steps'][-1], [{'prefix': 'Lower your eyes but keep your'}])
        self.assertTrue(route['recorded_ending'])


if __name__ == '__main__':
    unittest.main()

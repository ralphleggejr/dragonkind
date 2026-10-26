import json
import os
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch
from dragonbot.core import DEFAULT_CODE_REGEX, choose, extract_code, load_state, save_state, shadow, login_request_summary, refresh_global_pause


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
        for label in ('Lower your eyes and hold still', 'Lower your eyes in respect'):
            self.assertEqual(choose(['Run away', 'Attack', label], branches), 2)
        self.assertEqual(route['steps'][7], ['Head deeper into the forest'])
        self.assertEqual(choose(['Wait', 'Descend quickly in preparation for whatever’s coming your way'],
                                route['steps'][route['shadow_checkpoint']]), 1)

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

    def test_route_configuration(self):
        route = json.loads(Path('route.json').read_text())
        self.assertEqual(len(set(route['accounts'])), 8)
        self.assertEqual(route['cooldown_seconds'], 14400)
        self.assertEqual(route['steps'][route['shadow_checkpoint']],
                         ["Descend quickly in preparation for whatever's coming your way"])


if __name__ == '__main__':
    unittest.main()

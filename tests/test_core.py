import json
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from dragonbot.core import DEFAULT_CODE_REGEX, choose, extract_code, load_state, save_state, shadow


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

    def test_route_configuration(self):
        route = json.loads(Path('route.json').read_text())
        self.assertEqual(len(set(route['accounts'])), 8)
        self.assertEqual(route['cooldown_seconds'], 14400)
        self.assertEqual(route['steps'][route['shadow_checkpoint']],
                         ["Descend quickly in preparation for whatever's coming your way"])


if __name__ == '__main__':
    unittest.main()

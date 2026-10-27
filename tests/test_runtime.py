import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from dragonbot.runtime import AlreadyRunningError, next_attempt, state_lock


class Scheduling(unittest.TestCase):
    def test_next_attempt_keeps_account_deadlines_and_skips_pauses(self):
        state = {'accounts': {'a': {'next_run': 2000}, 'b': {'next_run': 1500},
                              'shadow': {'status': 'paused', 'next_run': 0}}}
        self.assertEqual(next_attempt(state, ['a', 'b', 'shadow'], 1000), ('b', 1500))
        self.assertEqual(next_attempt(state, ['a', 'b', 'shadow'], 1600), ('b', 1600))
        self.assertIsNone(next_attempt(state, ['shadow'], 1000))

    def test_only_one_worker_or_reset_can_own_a_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            path.write_text('{"accounts":{}}')
            with state_lock(path):
                with self.assertRaises(AlreadyRunningError):
                    with state_lock(path):
                        self.fail('Second worker acquired the lock')
            with state_lock(path):
                self.assertEqual(path.read_text(), '{"accounts":{}}')

    def test_idle_scheduler_opens_neither_browser_nor_driver(self):
        from dragonbot.__main__ import run
        state = {'accounts': {'a': {'status': 'cooldown', 'next_run': 15400}}, 'global_pause': False}
        with tempfile.TemporaryDirectory() as directory:
            with patch('dragonbot.__main__.configuration', return_value={'accounts': ['a']}), \
                 patch('dragonbot.__main__.load_state', return_value=state), \
                 patch('dragonbot.__main__.STATE', Path(directory) / 'state.json'), \
                 patch('dragonbot.__main__.time.time', return_value=1000), \
                 patch('dragonbot.__main__.time.sleep', side_effect=StopIteration) as wait, \
                 patch('playwright.sync_api.sync_playwright') as driver, \
                 patch('dragonbot.__main__.run_account') as play, redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(StopIteration):
                    run()
                driver.assert_not_called()
                play.assert_not_called()
                wait.assert_called_once_with(60)
                self.assertIn('Waiting with browsers closed', output.getvalue())

    def test_independent_deadlines_repeat_without_running_early_or_holding_driver(self):
        from dragonbot.__main__ import run
        now = [1000]
        state = {'accounts': {'a': {'status': 'cooldown', 'next_run': 1100},
                              'b': {'status': 'cooldown', 'next_run': 1200}}, 'global_pause': False}
        events, played = [], []
        driver = MagicMock()
        driver.__enter__.side_effect = lambda: events.append('driver-open') or MagicMock()
        driver.__exit__.side_effect = lambda *args: events.append('driver-closed')
        def play(engine, route, saved, account, headless):
            self.assertTrue(headless)
            played.append((account, now[0]))
            saved['accounts'][account] = {'status': 'cooldown', 'next_run': now[0] + 14400}
        def wait(seconds):
            self.assertLessEqual(seconds, 60)
            events.append('wait')
            if len(played) == 2:
                raise StopIteration
            now[0] += seconds
        with tempfile.TemporaryDirectory() as directory:
            with patch('dragonbot.__main__.configuration', return_value={'accounts': ['a', 'b']}), \
                 patch('dragonbot.__main__.load_state', return_value=state), \
                 patch('dragonbot.__main__.STATE', Path(directory) / 'state.json'), \
                 patch('dragonbot.__main__.time.time', side_effect=lambda: now[0]), \
                 patch('dragonbot.__main__.time.sleep', side_effect=wait), \
                 patch('playwright.sync_api.sync_playwright', return_value=driver), \
                 patch('dragonbot.__main__.run_account', side_effect=play):
                with self.assertRaises(StopIteration):
                    run()
        self.assertEqual(played, [('a', 1100), ('b', 1200)])
        self.assertEqual(state['accounts']['a']['next_run'], 15500)
        self.assertEqual(state['accounts']['b']['next_run'], 15600)
        self.assertEqual(events, ['wait', 'wait', 'driver-open', 'driver-closed', 'wait', 'wait',
                                  'driver-open', 'driver-closed', 'wait'])

    def test_desktop_headless_option_and_login_probe_forward_browser_mode(self):
        from dragonbot.__main__ import main
        with patch('dragonbot.__main__.run') as run, \
             patch('sys.argv', ['dragonbot', 'run-desktop', '--headless']), patch.dict('os.environ', {}):
            main()
            run.assert_called_once_with(headless=True, retry_rejected=False, resume_stories=False)
        with patch('dragonbot.__main__.replay_login', return_value=True) as probe, \
             patch('sys.argv', ['dragonbot', 'desktop-login', '--account', 'a@gmail.com', '--headless']):
            main()
            probe.assert_called_once_with('a@gmail.com', automatic=True, headless=True)

    def test_clean_stop_does_not_print_a_keyboard_interrupt_traceback(self):
        from dragonbot.__main__ import entrypoint
        with patch('dragonbot.__main__.main', side_effect=KeyboardInterrupt), \
             patch('dragonbot.__main__.signal.signal'), redirect_stdout(io.StringIO()) as output:
            entrypoint()
        self.assertIn('Saved cooldowns and pauses are retained', output.getvalue())


if __name__ == '__main__':
    unittest.main()

import io
from contextlib import redirect_stdout
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from dragonbot.macos_service import LABEL, manage, service_definition, validate_location


class MacService(unittest.TestCase):
    def test_service_allows_screen_sleep_and_restarts_only_failures(self):
        definition = service_definition('/tmp/My Dragonkind Project', headless=True)
        args = definition['ProgramArguments']
        self.assertEqual(args[:3], ['/usr/bin/caffeinate', '-i', '/tmp/My Dragonkind Project/.venv/bin/python'])
        self.assertEqual(args[-2:], ['run-desktop', '--headless'])
        self.assertTrue(definition['RunAtLoad'])
        self.assertEqual(definition['KeepAlive'], {'SuccessfulExit': False})
        self.assertNotIn('StartInterval', definition)  # The Python scheduler owns per-account cooldowns.
        self.assertNotIn('EnvironmentVariables', definition)

    def test_protected_macos_folders_are_not_used_for_the_launch_agent(self):
        for folder in ('Downloads', 'Desktop', 'Documents'):
            with self.subTest(folder=folder), self.assertRaises(RuntimeError):
                validate_location('/Users/test/' + folder + '/dragonkind', '/Users/test')
        validate_location('/Users/test/Projects/dragonkind', '/Users/test')

    def test_install_writes_safe_plist_with_absolute_paths_and_no_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            project = home / 'Projects/Dragonkind Project'
            (project / '.venv/bin').mkdir(parents=True)
            (project / '.venv/bin/python').touch()
            (project / '.env').write_text('MAIL_PASSWORD=PRIVATE_TEST_VALUE')
            def launch(*args, **kwargs):
                return subprocess.CompletedProcess(args, 1 if args[0] == 'print' else 0, '', '')
            with patch('dragonbot.macos_service.platform.system', return_value='Darwin'), \
                 patch('dragonbot.macos_service.Path.home', return_value=home), \
                 patch('dragonbot.macos_service.os.getuid', return_value=501), \
                 patch('dragonbot.macos_service.launchctl', side_effect=launch) as control, \
                 redirect_stdout(io.StringIO()) as output:
                manage('install', project, headless=True)
            plist = home / 'Library/LaunchAgents' / f'{LABEL}.plist'
            definition = plistlib.loads(plist.read_bytes())
            self.assertEqual(definition['WorkingDirectory'], str(project))
            self.assertNotIn('PRIVATE_TEST_VALUE', plist.read_text() + output.getvalue())
            self.assertEqual((project / '.env').read_text(), 'MAIL_PASSWORD=PRIVATE_TEST_VALUE')
            self.assertIn(('bootstrap', 'gui/501', str(plist)), [call.args for call in control.call_args_list])

    def test_status_excludes_other_launchctl_environment_output(self):
        result = subprocess.CompletedProcess([], 0, 'state = running\nSECRET_ENV=PRIVATE\npid = 10\n', '')
        with patch('dragonbot.macos_service.platform.system', return_value='Darwin'), \
             patch('dragonbot.macos_service.launchctl', return_value=result), redirect_stdout(io.StringIO()) as output:
            manage('status', '/tmp/project')
        self.assertIn('state = running', output.getvalue())
        self.assertNotIn('PRIVATE', output.getvalue())


if __name__ == '__main__':
    unittest.main()

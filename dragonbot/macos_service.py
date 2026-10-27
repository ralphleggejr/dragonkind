"""Install the native runner as a per-user macOS LaunchAgent."""
import argparse
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys
import tempfile

from .runtime import state_lock


LABEL = 'com.dragonkind.dragonbot'


def service_definition(project, headless=False):
    project = Path(project).resolve()
    arguments = ['/usr/bin/caffeinate', '-i', str(project / '.venv/bin/python'),
                 '-u', '-m', 'dragonbot', 'run-desktop']
    if headless:
        arguments.append('--headless')
    return {
        'Label': LABEL,
        'ProgramArguments': arguments,
        'WorkingDirectory': str(project),
        'RunAtLoad': True,
        'KeepAlive': {'SuccessfulExit': False},
        'ThrottleInterval': 60,
        'StandardOutPath': str(project / 'data/dragonbot.log'),
        'StandardErrorPath': str(project / 'data/dragonbot.log'),
    }


def validate_location(project, home):
    # LaunchAgents cannot rely on Terminal's access to macOS protected folders.
    for name in ('Downloads', 'Desktop', 'Documents'):
        try:
            Path(project).resolve().relative_to((Path(home) / name).resolve())
        except ValueError:
            continue
        raise RuntimeError('Move the project outside Downloads/Desktop/Documents first, for example to ~/Projects/dragonkind-git. Move the whole folder to retain .env and data.')


def launchctl(*arguments, required=True):
    result = subprocess.run(['/bin/launchctl', *arguments], capture_output=True, text=True)
    if required and result.returncode:
        raise RuntimeError(f'launchctl {arguments[0]} failed (exit {result.returncode}). Run this from your logged-in Mac account.')
    return result


def bootout_if_loaded(target):
    if launchctl('print', target, required=False).returncode == 0:
        launchctl('bootout', target)


def manage(action, project, headless=False):
    if platform.system() != 'Darwin':
        raise RuntimeError('This service installer requires macOS. Use Docker Compose on Windows.')
    project = Path(project).resolve()
    home = Path.home()
    domain = f'gui/{os.getuid()}'
    target = f'{domain}/{LABEL}'
    plist = home / 'Library/LaunchAgents' / f'{LABEL}.plist'
    if action == 'install':
        validate_location(project, home)
        if not (project / '.env').is_file():
            raise RuntimeError('The project needs its local .env file before installing the service.')
        if not (project / '.venv/bin/python').is_file():
            raise RuntimeError('Prepare the Python environment with: bash scripts/service-mac.sh install')
        bootout_if_loaded(target)
        with state_lock(project / 'data/state.json'):
            definition = service_definition(project, headless)
            plist.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=plist.parent, delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    plistlib.dump(definition, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.chmod(temporary, 0o600)
                    os.replace(temporary, plist)
                finally:
                    temporary.unlink(missing_ok=True)
        launchctl('enable', target)
        launchctl('bootstrap', domain, str(plist))
        print('Background service installed and started: ' + ('headless.' if headless else 'visible Chromium.'))
        print('It starts at user login and keeps idle system sleep off while allowing screen savers, display sleep, and locking.')
    elif action == 'start':
        if not plist.exists():
            raise RuntimeError('Install the service first with: bash scripts/service-mac.sh install')
        launchctl('enable', target)
        if launchctl('print', target, required=False).returncode != 0:
            launchctl('bootstrap', domain, str(plist))
        launchctl('kickstart', target)
        print('Background service started.')
    elif action == 'stop':
        bootout_if_loaded(target)
        launchctl('disable', target)
        print('Background service stopped and disabled. Use start or install to enable it again.')
    else:
        result = launchctl('print', target, required=False)
        if result.returncode:
            print('Background service is stopped or not installed.')
        else:
            for line in result.stdout.splitlines():
                if line.strip().startswith(('state =', 'pid =', 'last exit code =')):
                    print(line.strip())
    print(f'Log: {project / "data/dragonbot.log"}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['install', 'start', 'stop', 'status'], nargs='?', default='status')
    parser.add_argument('--headless', action='store_true')
    args = parser.parse_args()
    if args.headless and args.action != 'install':
        parser.error('--headless sets the browser mode when installing; reinstall to change it')
    try:
        manage(args.action, Path(__file__).resolve().parent.parent, args.headless)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)


if __name__ == '__main__':
    main()

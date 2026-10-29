from contextlib import contextmanager
from pathlib import Path
import os


class AlreadyRunningError(RuntimeError):
    pass


@contextmanager
def state_lock(state_path):
    path = Path(state_path).with_name('runner.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise AlreadyRunningError('Another bot is using this data folder. Stop it before starting or resetting accounts.') from None
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise AlreadyRunningError('Another bot is using this data folder. Stop it before starting or resetting accounts.') from None
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def account_deadline(record):
    return max(record.get('next_run', 0), record.get('retry_at', 0))


def next_attempt(state, accounts, now):
    candidates = [(max(now, account_deadline(state['accounts'].get(account, {}))), index, account)
                  for index, account in enumerate(accounts)
                  if state['accounts'].get(account, {}).get('status') != 'paused']
    if not candidates:
        return None
    when, _, account = min(candidates)
    return account, when

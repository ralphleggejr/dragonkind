"""Account retries preserve site cooldowns and never clear protected story pauses."""
from datetime import datetime

RETRY_BASE_SECONDS = 300
RETRY_MAX_SECONDS = 14400


def protected_pause(record):
    reason = record.get('reason', '').casefold()
    checks = record.get('tree_checkpoint_checks', [])
    return (record.get('pause_kind') in ('shadow', 'unexpected_options', 'manual')
            or 'mentions shadow' in reason or reason.startswith('tree checkpoint')
            or any(check.get('shadow_found') or check.get('option_count', 0) > 2 for check in checks))


def retryable_failure(record):
    if protected_pause(record):
        return False
    reason = record.get('reason', '')
    return (record.get('status') in ('active', 'logging_out')
            or reason.startswith(('Failure while ', 'Interrupted run;', 'Account already has a game in progress;')))


def schedule_retry(record, reason, now):
    count = record.get('retry_attempts', 0) + 1
    delay = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * 2 ** min(count - 1, 6))
    record.update(status='retry_wait', reason=reason, retry_attempts=count,
                  retry_at=max(now + delay, record.get('next_run', 0)))
    record.pop('pause_scope', None)
    record.setdefault('retry_history', []).append({
        'failed_at': datetime.fromtimestamp(now).astimezone().isoformat(),
        'reason': reason, 'attempt': count, 'retry_at': record['retry_at']})
    record['retry_history'] = record['retry_history'][-10:]


def clear_retry_after_success(record):
    for key in ('retry_at', 'retry_attempts', 'last_error_notified_reason', 'reason', 'pause_scope'):
        record.pop(key, None)

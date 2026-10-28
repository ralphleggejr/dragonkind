"""Persist a reporting round across staggered account cooldowns and restarts."""
from copy import deepcopy
from datetime import datetime
from uuid import uuid4


def result_snapshot(state, account, attempted):
    record = state['accounts'].get(account, {})
    return {'finished_at': datetime.now().astimezone().isoformat(), 'attempted': attempted,
            **deepcopy({key: record[key] for key in ('status', 'reason', 'next_run',
                'tree_checkpoint_checks', 'notification_details') if key in record})}


def begin_rotation(state, accounts):
    rotation = state.get('reporting_rotation')
    if rotation is None:
        rotation = {'id': uuid4().hex, 'started_at': datetime.now().astimezone().isoformat(),
                    'accounts': list(accounts), 'results': {}}
        state['reporting_rotation'] = rotation
    for account in accounts:
        if account not in rotation['accounts']:
            rotation['accounts'].append(account)
    for account in rotation['accounts']:
        if account in rotation['results']:
            continue
        if account not in accounts:
            rotation['results'][account] = [{'status': 'removed', 'attempted': False}]
        elif state['accounts'].get(account, {}).get('status') == 'paused':
            rotation['results'][account] = [result_snapshot(state, account,
                attempted=rotation.get('in_progress') == account)]
    return rotation


def record_result(state, accounts, account):
    rotation = begin_rotation(state, accounts)
    results = rotation['results'].setdefault(account, [])
    # Replace the placeholder if an explicitly resumed account runs in this round.
    if results and not any(result.get('attempted') for result in results):
        results.clear()
    results.append(result_snapshot(state, account, attempted=True))
    rotation.pop('in_progress', None)


def recover_finished_result(state, accounts):
    rotation = state.get('reporting_rotation', {})
    account = rotation.get('in_progress')
    # Recover a run that saved its final status just before the worker restarted.
    if account in accounts and state['accounts'].get(account, {}).get('status') in ('cooldown', 'paused'):
        record_result(state, accounts, account)


def rotation_ready(state):
    rotation = state.get('reporting_rotation')
    return bool(rotation) and all(rotation['results'].get(account) for account in rotation['accounts'])


def rotation_summary(state, format_checks, aborted=False):
    rotation = state['reporting_rotation']
    total = len(rotation['accounts'])
    handled = sum(bool(rotation['results'].get(account)) for account in rotation['accounts'])
    outcome = 'STOPPED EARLY' if aborted else 'COMPLETE'
    lines = [f'Dragonkind account rotation: {outcome}', f'Started: {rotation["started_at"]}',
             f'Finished: {datetime.now().astimezone().isoformat()}',
             f'Accounts handled: {handled} of {total}',
             'Shadow detections are emailed immediately; all other checks are grouped in this summary.']
    for index, account in enumerate(rotation['accounts'], 1):
        lines.extend(['', '=' * 48, f'{index}. {account}'])
        results = rotation['results'].get(account, [])
        if not results:
            lines.append('NOT ATTEMPTED: rotation stopped before this account was handled.')
            results = [result_snapshot(state, account, attempted=False)]
        for number, result in enumerate(results, 1):
            lines.extend([f'Attempt {number}: {result.get("status", "pending")}',
                          'Attempted this round: ' + ('YES' if result.get('attempted') else 'NO')])
            if result.get('finished_at'):
                lines.append(f'Recorded at: {result["finished_at"]}')
            if result.get('reason'):
                lines.append(f'Reason: {result["reason"]}')
            deadline = result.get('next_run', 0)
            lines.append('Next eligible: ' + (datetime.fromtimestamp(deadline).astimezone().isoformat()
                                             if deadline else 'no saved cooldown'))
            checks = result.get('tree_checkpoint_checks', [])
            if checks and (result.get('attempted') or result.get('status') == 'paused'):
                lines.append(format_checks(account, checks, result.get('status', 'pending').upper()))
            else:
                lines.append('No tree checkpoint checks recorded for this attempt.')
    for report in state.get('deferred_tree_reports', []):
        lines.extend(['', 'Previously queued individual check (included here instead of sent separately):', report['body']])
    return f'Dragonkind rotation {outcome}: {handled}/{total} accounts', '\n'.join(lines)

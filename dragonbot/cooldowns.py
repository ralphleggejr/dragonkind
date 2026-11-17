"""Validate evidence from the site's cooldown panel before scheduling replay."""
from datetime import datetime
from .core import StoryPositionError


class CooldownEvidenceError(StoryPositionError):
    pass


def cooldown_evidence(observation, now, fallback_seconds):
    timestamps = observation['countdown_datetimes']
    if len(timestamps) > 1:
        raise CooldownEvidenceError('Multiple countdowns found in the cooldown panel')
    raw = timestamps[0] if timestamps else None
    if raw is not None:
        try:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            raise CooldownEvidenceError('The site cooldown deadline is invalid') from None
        if parsed.tzinfo is None:
            raise CooldownEvidenceError('The site cooldown deadline has no timezone')
        deadline = parsed.timestamp()
        if deadline <= now:
            raise CooldownEvidenceError('The displayed site cooldown deadline has already elapsed')
        source = 'site_countdown'
    else:
        # The panel itself must already have been visibly confirmed by the caller.
        deadline = now + fallback_seconds
        source = 'visible_panel_conservative_fallback'
    return {'confirmed_at': datetime.fromtimestamp(now).astimezone().isoformat(),
            'confirmed_at_epoch': now, 'observed_deadline': deadline, 'source': source,
            'site_countdown_datetime': raw,
            'label': observation['label'], 'countdown_text': observation['countdown_text']}

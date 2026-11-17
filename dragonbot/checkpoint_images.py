"""Portable, immutable screenshot evidence for checkpoint email attachments."""
import hashlib
import os
from pathlib import Path
import re
import tempfile
import time
from datetime import datetime
from uuid import uuid4
from .core import StoryPositionError

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
# Leave room for all nine captures while retaining compatibility with older 2 MiB files.
MAX_CAPTURE_BYTES = MAX_TOTAL_BYTES // 9
RETENTION_SECONDS = 7 * 24 * 3600
PNG_HEADER = b'\x89PNG\r\n\x1a\n'


class ScreenshotEvidenceError(StoryPositionError):
    pass


OPTION_BOUNDS_JS = """(elements) => {
    const rects = elements.flatMap(element => [...element.getClientRects()])
        .filter(rect => rect.width > 0 && rect.height > 0);
    if (!rects.length) return null;
    const root = document.documentElement;
    const left = Math.max(0, Math.min(...rects.map(rect => rect.left)) + scrollX - 12);
    const top = Math.max(0, Math.min(...rects.map(rect => rect.top)) + scrollY - 12);
    const right = Math.min(Math.max(root.scrollWidth, innerWidth), Math.max(...rects.map(rect => rect.right)) + scrollX + 12);
    const bottom = Math.min(Math.max(root.scrollHeight, document.body.scrollHeight, innerHeight),
                            Math.max(...rects.map(rect => rect.bottom)) + scrollY + 12);
    return {x: left, y: top, width: right - left, height: bottom - top};
}"""


def image_root(state_path):
    return Path(state_path).parent / 'checkpoint-screenshots'


def save_checkpoint_png(state_path, account, data, snapshot, stage):
    if not isinstance(data, bytes) or not data.startswith(PNG_HEADER) or len(data) < 24:
        raise ScreenshotEvidenceError('Checkpoint capture did not produce a PNG image')
    if len(data) > min(MAX_IMAGE_BYTES, MAX_CAPTURE_BYTES):
        raise ScreenshotEvidenceError('Checkpoint image exceeds the email attachment size limit')
    root = image_root(state_path)
    root.mkdir(parents=True, exist_ok=True)
    filename = uuid4().hex + '.png'
    fd, temporary_name = tempfile.mkstemp(dir=root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / filename)
    finally:
        temporary.unlink(missing_ok=True)
    return {'file': filename, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
            'account': account, 'captured_at': datetime.now().astimezone().isoformat(), 'stage': stage,
            'option_count': len(snapshot['options']), 'visible_count': snapshot['visible_count'],
            'option_fingerprint': [[option['control'], option['text']] for option in snapshot['options']],
            'all_visible': snapshot['all_visible'], 'animations_complete': snapshot['animations_complete'],
            'verified_after_capture': False}


def read_checkpoint_png(attachment, root):
    filename = attachment.get('file', '')
    if not isinstance(filename, str) or not re.fullmatch(r'[a-f0-9]{32}\.png', filename):
        raise ScreenshotEvidenceError('Checkpoint image filename is invalid')
    root = Path(root).resolve()
    path = (root / filename).resolve()
    if path.parent != root:
        raise ScreenshotEvidenceError('Checkpoint image is outside the screenshot folder')
    try:
        with path.open('rb') as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
    except OSError:
        raise ScreenshotEvidenceError('Checkpoint image is missing or unreadable') from None
    if (len(data) > MAX_IMAGE_BYTES or not data.startswith(PNG_HEADER) or len(data) < 24
            or len(data) != attachment.get('bytes')
            or hashlib.sha256(data).hexdigest() != attachment.get('sha256')):
        raise ScreenshotEvidenceError('Checkpoint image failed its integrity or size check')
    return data


def rotation_screenshots(rotation):
    attachments, missing = [], []
    for index, account in enumerate(rotation['accounts'], 1):
        results = rotation['results'].get(account, [])
        image = next((result['tree_checkpoint_screenshot'] for result in reversed(results)
                      if result.get('attempted') and result.get('tree_checkpoint_screenshot')), None)
        if image is None or image.get('account') != account:
            missing.append(account)
            continue
        safe_account = re.sub(r'[^a-zA-Z0-9._-]', '_', account)
        attachments.append({**image, 'account': account,
                            'name': f'{index:02d}-{safe_account}-tree-options.png'})
    return attachments, missing


def prune_checkpoint_images(state, root, now=None):
    # Pending reports, active rounds and each account's latest evidence are pinned.
    pinned = set()
    def collect(value):
        if isinstance(value, dict):
            if isinstance(value.get('file'), str):
                pinned.add(value['file'])
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect(state)
    cutoff = (time.time() if now is None else now) - RETENTION_SECONDS
    for path in Path(root).glob('*.png'):
        if not re.fullmatch(r'[a-f0-9]{32}\.png', path.name):
            continue
        try:
            if path.name not in pinned and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass

"""Durable resumable upload; ambiguous completion never starts another upload."""
import hashlib
import json
import os
import time
from pathlib import Path
from src.session_crypto import session_cipher
from src.pipeline_control import ControlError

URL = 'https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status'
CHUNK = 1024 * 1024


def upload(youtube, path, body, control, episode_id=None):
    cipher = session_cipher()
    size = Path(path).stat().st_size
    if size <= 0 or size > 50 * 1024 * 1024:
        raise ControlError('upload_file_size_limit')
    sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    state = control.upload()
    if state.get('state') == 'uploaded':
        return state['video_id']
    if state.get('sha256') and state['sha256'] != sha:
        raise ControlError('upload_file_changed')
    if state.get('state') in ('unknown', 'session_creating'):
        raise ControlError('upload_unknown_manual_review_required')
    http = youtube._http
    # Transport requests must finish well within the slot lease.
    http.timeout = 60
    if not state or state.get("state") == "prepared":
        control.upload({'state': 'session_creating', 'sha256': sha, 'size': size,
                        'episode_id': episode_id, 'started_at': time.time(), 'failures': 0})
        try:
            response, _ = http.request(URL, method='POST', body=json.dumps(body), headers={
                'Content-Type': 'application/json', 'X-Upload-Content-Length': str(size),
                'X-Upload-Content-Type': 'video/mp4'})
            if int(response.status) != 200 or not response.get('location'):
                raise ControlError('upload_session_creation_failed')
            uri = response['location']
            state = control.upload({'state': 'session_ready',
                                    'session': cipher.encrypt(uri.encode()).decode()})
        except Exception:
            # session_creating is already durable if the DB is unavailable here.
            raise ControlError('upload_session_unknown') from None
    else:
        uri = cipher.decrypt(state['session'].encode()).decode()
    if not uri.startswith('https://www.googleapis.com/upload/'):
        raise ControlError('invalid_upload_session_uri')
    if int(state.get('failures', 0)) >= 3:
        raise ControlError('upload_retry_limit')
    offset = None  # Query status before every restart, including ready sessions.
    last_progress = time.monotonic()
    while True:
        state = control.upload()
        if time.time() - state['started_at'] > 900 or time.monotonic() - last_progress > 120:
            control.upload({'state': 'unknown'})
            raise ControlError('upload_deadline_exceeded')
        try:
            if offset is None:
                headers = {'Content-Length': '0', 'Content-Range': f'bytes */{size}'}
                response, content = http.request(uri, method='PUT', body=b'', headers=headers)
            else:
                with open(path, 'rb') as stream:
                    stream.seek(offset)
                    blob = stream.read(CHUNK)
                if not blob:
                    offset = None
                    continue
                headers = {'Content-Type': 'video/mp4', 'Content-Length': str(len(blob)),
                           'Content-Range': f'bytes {offset}-{offset+len(blob)-1}/{size}'}
                response, content = http.request(uri, method='PUT', body=blob, headers=headers)
            status = int(response.status)
            if status in (200, 201):
                video_id = json.loads(content).get('id')
                if not video_id:
                    raise ControlError('upload_completion_missing_id')
                control.upload({'state': 'uploaded', 'video_id': video_id})
                return video_id
            if status == 308:
                confirmed = int(response.get('range', 'bytes=0--1').split('-')[-1]) + 1 if response.get('range') else 0
                if confirmed > size or (offset is not None and confirmed < offset):
                    raise ControlError('upload_invalid_range')
                if offset is not None and confirmed == offset:
                    failures = int(state.get('failures', 0)) + 1
                    control.upload({'failures': failures})
                    if failures >= 3:
                        raise ControlError('upload_no_progress_limit')
                if confirmed > (offset or 0):
                    last_progress = time.monotonic()
                    control.upload({'bytes_confirmed': confirmed, 'failures': 0, 'state': 'in_progress'})
                offset = confirmed
                continue
            if status not in (500, 502, 503, 504, 429):
                control.upload({'state': 'unknown'})
                raise ControlError('upload_result_unknown')
            raise OSError('transient_upload_error')
        except ControlError:
            raise
        except Exception:
            failures = int(state.get('failures', 0)) + 1
            control.upload({'failures': failures})
            if failures >= 3:
                raise ControlError('upload_retry_limit') from None
            offset = None
            time.sleep(min(2 ** failures, 8))

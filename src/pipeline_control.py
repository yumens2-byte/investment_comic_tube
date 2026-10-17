"""Atomic daily controls. Opt-in until the reviewed SQL and private bucket exist."""
import contextvars
import hashlib
import json
import os
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from src.db_client import get_client

_current = contextvars.ContextVar('pipeline_control', default=None)


class ControlError(RuntimeError):
    def __init__(self, reason, details=None):
        super().__init__(reason)
        self.details = details or {}


def enabled():
    return os.getenv('OPERATIONAL_SAFETY_ENABLED', '').lower() == 'true'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()


class Control:
    def __init__(self, group, hero):
        self.group, self.hero = group, hero
        self.channel = os.environ.get('PIPELINE_CHANNEL_KEY') or 'default'
        self.day = datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
        self.owner = uuid.uuid4().hex
        self.fence = None
        self.slot = f'{self.channel}/{self.day}/{group}'

    def rpc(self, action, payload=None):
        try:
            data = get_client().rpc('pipeline_control_v1', {
                'p_channel': self.channel, 'p_day': self.day, 'p_group': self.group,
                'p_owner': self.owner, 'p_fence': self.fence,
                'p_action': action, 'p_payload': payload or {},
            }).execute().data
        except Exception as exc:
            raise ControlError('persistent_control_unavailable') from exc
        if not isinstance(data, dict) or data.get('error'):
            reason = data.get('error', 'invalid_control_response') if isinstance(data, dict) else 'invalid_control_response'
            details = {}
            if reason == 'generation_limit_reached':
                details = {"day": self.day, **(payload or {})}
                details.pop('input_hash', None)
                try:
                    status = self.status()
                    details.update(counts=status.get('counts'), limits=status.get('limits'))
                except ControlError:
                    pass
            raise ControlError(reason, details)
        return data

    def claim(self):
        if self.group == 'production':
            status = self.status()
            if status.get('version') != 2:
                raise ControlError('caption_recovery_schema_required')
        result = self.rpc('claim', {'hero': self.hero})
        self.fence = result['fence']
        if self.group == 'production' and result['hero'] != self.hero:
            raise ControlError('daily_hero_conflict')
        return result

    def reserve(self, stage, asset, value):
        if self.day != datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat():
            raise ControlError('historical_generation_forbidden')
        return self.rpc('reserve', {'stage': stage, 'asset': str(asset),
                                   'input_hash': fingerprint(value), 'hero': self.hero})

    def upload(self, patch=None):
        return self.rpc('upload', patch or {})

    def asset(self, key, patch=None):
        return self.rpc('asset', {'key': key, 'patch': patch or {}})

    def release(self):
        self.rpc('release')

    def status(self):
        try:
            data = get_client().rpc('pipeline_status_v1', {
                'p_channel': self.channel, 'p_day': self.day, 'p_group': self.group,
            }).execute().data
        except Exception as exc:
            raise ControlError('caption_recovery_schema_required') from exc
        if not isinstance(data, dict) or data.get('version') != 2:
            raise ControlError('invalid_control_status')
        return data

    def bind_episode(self, script):
        if self.group != 'production':
            raise ControlError('pilot_episode_write_forbidden')
        try:
            data = get_client().rpc('pipeline_bind_episode_v1', {
                'p_channel': self.channel, 'p_day': self.day, 'p_group': self.group,
                'p_owner': self.owner, 'p_fence': self.fence, 'p_script': script,
            }).execute().data
        except Exception as exc:
            raise ControlError('episode_binding_unavailable') from exc
        if not isinstance(data, dict) or not data.get('episode_id') or data.get('error'):
            raise ControlError(data.get('error', 'episode_binding_invalid') if isinstance(data, dict)
                               else 'episode_binding_invalid')
        return data['episode_id']


def activate(control):
    return _current.set(control)


def reset(token):
    _current.reset(token)


def current():
    return _current.get()


def reserve(stage, asset, value):
    control = current()
    if control:
        control.reserve(stage, asset, value)


def generate(client, stage, asset, **kwargs):
    if not current():
        return client.models.generate_content(**kwargs)
    reserve(stage, asset, kwargs)
    from google.genai import types
    config = kwargs.get('config')
    if config is None:
        config = types.GenerateContentConfig()
    config.automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)
    kwargs['config'] = config
    return client.models.generate_content(**kwargs)


def make_client(key):
    from google import genai
    from google.genai import types
    return genai.Client(api_key=key, http_options=types.HttpOptions(
        timeout=120000, retry_options=types.HttpRetryOptions(attempts=1)))


def protected(group, forced_hero=None):
    """Activate only after operator enables schema-backed controls."""
    from functools import wraps
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not enabled():
                return function(*args, **kwargs)
            import logging
            import threading
            from src.daily_hero import select_daily_hero
            hero = forced_hero or kwargs.get('track') or (args[0] if args and isinstance(args[0], str) else None) or select_daily_hero()
            mode = 'full-preview' if kwargs.get('video_pilot') else group
            control = Control(mode, hero)
            token = None
            stop = threading.Event()
            errors = []
            def heartbeat():
                while not stop.wait(60):
                    try:
                        control.rpc('heartbeat')
                    except Exception as exc:
                        errors.append(exc)
                        break
            try:
                control.claim()
                from src.asset_store import verify_store
                verify_store()
                from src.caption_policy import adaptive
                if adaptive():
                    from src.caption_layout import font_identity
                    font_identity()
                worker = threading.Thread(target=heartbeat, daemon=True)
                worker.start()
                # Verify encryption configuration before production paid calls.
                if mode == 'production':
                    from src.session_crypto import session_cipher
                    session_cipher()
                    receipt = control.upload()
                    if receipt.get('state') == 'uploaded':
                        from src.drive_manager import update_episode
                        update_episode(receipt['episode_id'], status=receipt.get('episode_status', 'published'),
                                       youtube_video_id=receipt['video_id'])
                        return 0
                    if receipt.get('state') in ('unknown', 'session_creating'):
                        raise ControlError('upload_unknown_manual_review_required')
                    if receipt:
                        # Restore the original rendered file and metadata, never regenerate on recovery.
                        from src.asset_store import restore
                        from src.publisher import upload_to_youtube
                        token = activate(control)
                        if not restore('video', 'final', receipt['metadata'], 'output_short.mp4'):
                            raise ControlError('upload_asset_unavailable')
                        metadata = receipt['metadata']
                        video_id = upload_to_youtube('output_short.mp4', metadata)
                        if not video_id:
                            raise ControlError('youtube_recovery_no_video_id')
                        from src.drive_manager import update_episode
                        update_episode(receipt['episode_id'], status=receipt.get('episode_status', 'published'),
                                       youtube_video_id=video_id)
                        return 0
                if token is None:
                    token = activate(control)
                result = function(*args, **kwargs)
                if errors:
                    raise ControlError('heartbeat_failed')
                return result
            except Exception:
                logging.getLogger(__name__).exception('operational_safety_failed')
                return 1
            finally:
                stop.set()
                if token is not None:
                    reset(token)
                if control.fence is not None:
                    try:
                        control.release()
                    except Exception:
                        logging.getLogger(__name__).warning('control_release_failed')
        return wrapped
    return decorate

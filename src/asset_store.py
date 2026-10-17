"""Private durable assets, with integrity checks; never public URLs."""
import hashlib
import os
import json
from pathlib import Path
import requests
from src.pipeline_control import current, fingerprint, ControlError


def _request(method, key, data=None):
    try:
        base = os.environ['SUPABASE_URL'].rstrip('/')
        secret = os.environ['SUPABASE_SERVICE_ROLE_KEY']
        response = requests.request(method, f'{base}/storage/v1/object/pipeline-assets/{key}',
            headers={'Authorization': f'Bearer {secret}', 'apikey': secret,
                     'Content-Type': 'application/octet-stream', 'x-upsert': 'true'},
            data=data, timeout=60)
    except (KeyError, requests.RequestException) as exc:
        raise ControlError('private_asset_storage_unavailable') from exc
    if not response.ok:
        raise ControlError('private_asset_storage_unavailable')
    return response.content


def restore(stage, asset, inputs, path):
    control = current()
    if not control:
        return False
    record = control.asset(f'{stage}:{asset}')
    if record.get('input_hash') != fingerprint(inputs):
        return False
    blob = _request('GET', record['key'])
    if hashlib.sha256(blob).hexdigest() != record['checksum']:
        raise ControlError('private_asset_checksum_mismatch')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(blob)
    return True


def save(stage, asset, inputs, path):
    control = current()
    if not control:
        return
    blob = Path(path).read_bytes()
    checksum = hashlib.sha256(blob).hexdigest()
    key = f'{fingerprint(control.slot)}/{stage}/{asset}/{checksum}'
    _request('POST', key, blob)
    control.asset(f'{stage}:{asset}', {'input_hash': fingerprint(inputs), 'key': key,
                                      'checksum': checksum})


def verify_store():
    base = os.environ['SUPABASE_URL'].rstrip('/')
    secret = os.environ['SUPABASE_SERVICE_ROLE_KEY']
    response = requests.get(f'{base}/storage/v1/bucket/pipeline-assets',
                            headers={'Authorization': f'Bearer {secret}', 'apikey': secret}, timeout=15)
    if not response.ok or response.json().get('public') is not False:
        raise ControlError('private_asset_bucket_required')


def read_json(stage, asset, inputs):
    control = current()
    if not control:
        return None
    record = control.asset(f'{stage}:{asset}')
    if not isinstance(record, dict) or not record:
        return None
    if record.get('input_hash') != fingerprint(inputs):
        raise ControlError('checkpoint_input_changed')
    blob = _request('GET', record['key'])
    if hashlib.sha256(blob).hexdigest() != record['checksum']:
        raise ControlError('private_asset_checksum_mismatch')
    try:
        return json.loads(blob)
    except (ValueError, UnicodeError) as exc:
        raise ControlError('checkpoint_json_invalid') from exc


def write_json(stage, asset, inputs, value):
    control = current()
    if not control:
        return
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')
    checksum = hashlib.sha256(blob).hexdigest()
    key = f'{fingerprint(control.slot)}/{stage}/{asset}/{checksum}'
    _request('POST', key, blob)
    control.asset(f'{stage}:{asset}', {'input_hash': fingerprint(inputs), 'key': key,
                                     'checksum': checksum})

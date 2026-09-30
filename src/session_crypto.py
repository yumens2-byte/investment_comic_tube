"""Separate-purpose session encryption, derived only inside the server runner."""
import base64
import hashlib
import hmac
import os
from cryptography.fernet import Fernet


def session_cipher():
    explicit = os.environ.get('UPLOAD_SESSION_FERNET_KEY')
    if explicit:
        return Fernet(explicit.encode())
    secret = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
    if not secret or len(secret) < 32:
        raise ValueError('server_secret_required_for_session_encryption')
    channel = os.environ.get('PIPELINE_CHANNEL_KEY') or 'default'
    key = hmac.new(secret.encode(), ('investment-comic/upload-session/v1/' + channel).encode(), hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(key))

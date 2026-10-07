"""A shared manual sender switch, not an automatic failover lease."""
from __future__ import annotations

import base64
import json
import os
import re
from urllib.parse import urlsplit

import requests


class SenderPolicyError(RuntimeError):
    """The shared switch cannot be safely read; no SMTP submission is allowed."""


class InactiveSender(SenderPolicyError):
    pass


def validate_policy_url(url: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'api.github.com'
            or not re.fullmatch(r'/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/contents/config\.sender\.json', parsed.path)
            or parsed.query != 'ref=main' or parsed.fragment):
        raise ValueError('发送端开关必须使用 GitHub API 的 main/config.sender.json。')


def read_policy(url: str) -> dict:
    validate_policy_url(url)
    headers = {'Accept': 'application/vnd.github+json',
               'Cache-Control': 'no-cache', 'User-Agent': 'AdmissionRadar/manual-sender-switch'}
    # GitHub runners already receive an ephemeral token; VPS public reads need none.
    token = os.environ.get('ADMISSION_RADAR_POLICY_TOKEN', '')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        with requests.Session() as session:
            response = session.get(url, headers=headers, timeout=10, allow_redirects=False)
            if response.status_code != 200:
                raise SenderPolicyError('sender_policy_http_' + str(response.status_code))
            if len(response.content) > 65536:
                raise SenderPolicyError('sender_policy_too_large')
            envelope = response.json()
        if envelope.get('type') != 'file' or envelope.get('encoding') != 'base64':
            raise ValueError('invalid contents envelope')
        encoded = ''.join(envelope['content'].split())
        raw = base64.b64decode(encoded, validate=True)
        policy = json.loads(raw)
        if (not isinstance(policy, dict) or type(policy.get('schema_version')) is not int
                or policy['schema_version'] != 1 or policy.get('active_sender') not in ('github', 'vps')):
            raise ValueError('invalid sender switch')
        sha = envelope['sha']
        if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}', sha):
            raise ValueError('invalid blob sha')
        return {'active_sender': policy['active_sender'], 'policy_sha': sha}
    except SenderPolicyError:
        raise
    except Exception:
        # Never retain server response bodies, request headers or tokens in errors.
        raise SenderPolicyError('sender_policy_unavailable_or_invalid') from None


def sender_status(config) -> dict:
    if not config.sender_id or not config.sender_policy_url:
        raise SenderPolicyError('sender_policy_not_configured')
    policy = read_policy(config.sender_policy_url)
    return dict(sender_id=config.sender_id, **policy,
                may_send=policy['active_sender'] == config.sender_id)


def require_sender(config) -> None:
    if not config.sender_id and not config.sender_policy_url:
        return  # Existing single-host profiles remain supported.
    if not sender_status(config)['may_send']:
        raise InactiveSender('sender_is_standby')

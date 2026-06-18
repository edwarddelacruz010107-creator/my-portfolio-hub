"""
app/services/resend_service.py — Resend Email Service (v4.1)

All transactional email dispatched through the Resend API:
  • OTP / password reset
  • Welcome / verification
  • Subscription lifecycle (activated, renewed, expiring, expired)
  • Payment notifications (approved, rejected)
  • System notifications

The RESEND_API_KEY is:
  - Read from GlobalEmailConfig (DB, Fernet-encrypted) first
  - Falls back to RESEND_API_KEY environment variable
  - NEVER exposed to any template or JavaScript context

SMTP (Flask-Mail) remains as final fallback for all email types.

API reference: https://resend.com/docs/api-reference/emails/send-email
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests
from flask import current_app

logger = logging.getLogger(__name__)

_RESEND_ENDPOINT = 'https://api.resend.com/emails'
_TIMEOUT         = 15  # seconds


# ─────────────────────────────────────────────────────────────────────────────
# Key resolution (always server-side)
# ─────────────────────────────────────────────────────────────────────────────

def _get_resend_key() -> str:
    """
    Resolve the active Resend API key.

    Priority:
      1. GlobalEmailConfig.resend_api_key (DB, encrypted)
      2. RESEND_API_KEY environment variable
    """
    try:
        from app.models.portfolio import GlobalEmailConfig
        cfg = GlobalEmailConfig.get()
        if cfg.resend_api_key:
            return cfg.resend_api_key
    except Exception as exc:
        logger.debug('resend_service: could not load GlobalEmailConfig: %s', exc)
    return os.environ.get('RESEND_API_KEY', '').strip()


def _get_sender(cfg=None) -> str:
    """Return From address for Resend — must be a verified domain."""
    try:
        if cfg is None:
            from app.models.portfolio import GlobalEmailConfig
            cfg = GlobalEmailConfig.get()
        name  = cfg.sender_name  or 'Portfolio CMS'
        email = cfg.sender_email or os.environ.get('RESEND_FROM_EMAIL', 'noreply@portfoliocms.app')
        return f'{name} <{email}>'
    except Exception:
        return 'Portfolio CMS <noreply@portfoliocms.app>'


# ─────────────────────────────────────────────────────────────────────────────
# Core send primitive
# ─────────────────────────────────────────────────────────────────────────────

def _send_via_resend(
    to: str,
    subject: str,
    text: str,
    html: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> tuple[bool, str]:
    """
    POST to Resend /emails.
    Returns (success: bool, message_id_or_error: str).
    API key is injected server-side — never sent to client.
    """
    api_key = _get_resend_key()
    if not api_key:
        return False, 'Resend API key not configured.'

    payload: dict = {
        'from':    _get_sender(),
        'to':      [to],
        'subject': subject,
        'text':    text,
    }
    if html:
        payload['html'] = html
    if reply_to:
        payload['reply_to'] = reply_to

    try:
        resp = requests.post(
            _RESEND_ENDPOINT,
            json=payload,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type':  'application/json',
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            msg_id = data.get('id', 'unknown')
            logger.info('Resend: sent <%s> to %s (id=%s)', subject[:60], to, msg_id)
            return True, msg_id
        body = resp.json()
        err  = body.get('message') or body.get('error', f'HTTP {resp.status_code}')
        logger.error('Resend API error [%s]: %s — payload keys: %s',
                     resp.status_code, err, list(payload.keys()))
        return False, err
    except requests.Timeout:
        logger.error('Resend: timeout after %ds sending to %s', _TIMEOUT, to)
        return False, f'Timeout contacting Resend API after {_TIMEOUT}s.'
    except Exception as exc:
        logger.exception('Resend: unexpected error sending to %s: %s', to, exc)
        return False, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# SMTP fallback — REMOVED in v5.0
# ─────────────────────────────────────────────────────────────────────────────

def _smtp_fallback(to: str, subject: str, body: str) -> bool:
    """
    Deprecated stub.  Flask-Mail / SMTP was removed in v5.0.

    All email now routes through MailerSend exclusively.
    Callers that still reference this function (e.g. renewal_scheduler)
    receive False so they log a warning rather than crashing.

    Migration: replace any call to _smtp_fallback() with
    app.services.mailersend_service.send_email() directly.
    """
    logger.warning(
        '_smtp_fallback() called for <%s> to %s — '
        'SMTP is removed in v5.0. Configure MAILERSEND_API_KEY instead.',
        subject[:60], to,
    )
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Connection validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_resend_key(key: str) -> tuple[bool, str]:
    """
    Validate a Resend API key by calling GET /domains (low-cost endpoint).
    Returns (valid: bool, message: str).
    """
    if not key or len(key) < 10:
        return False, 'Key too short.'
    try:
        resp = requests.get(
            'https://api.resend.com/domains',
            headers={'Authorization': f'Bearer {key}'},
            timeout=10,
        )
        if resp.status_code == 200:
            return True, 'Connected successfully.'
        if resp.status_code == 401:
            return False, 'Invalid API key — authentication failed.'
        return False, f'Unexpected response: HTTP {resp.status_code}'
    except requests.Timeout:
        return False, 'Connection timed out.'
    except Exception as exc:
        return False, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Public email functions
# ─────────────────────────────────────────────────────────────────────────────

def send_otp_email(
    recipient_email: str,
    otp: str,
    user_type: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    ttl_minutes: int = 10,
) -> bool:
    """
    Send OTP password-reset code via Resend (SMTP fallback).

    Args:
        recipient_email: Destination address.
        otp:             Raw 6-digit OTP (generated by otp_service).
        user_type:       'superadmin' | 'admin' | 'tenant'
        ip_address:      Request IP for transparency.
        user_agent:      Request UA for transparency.
        ttl_minutes:     OTP validity window shown in email.

    Returns True on success.
    """
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    role_label = user_type.replace('_', ' ').title()

    subject = f'[Portfolio CMS] Your Password Reset OTP — {now}'
    text = (
        f'Hello,\n\n'
        f'A password reset was requested for your Portfolio CMS account ({role_label}).\n\n'
        f'Your one-time password (OTP) is:\n\n'
        f'    {otp}\n\n'
        f'This OTP expires in {ttl_minutes} minutes.\n'
        f'Do NOT share it with anyone.\n\n'
        f'Request details:\n'
        f'  IP address : {ip_address or "unknown"}\n'
        f'  User agent : {(user_agent or "unknown")[:120]}\n'
        f'  Time (UTC) : {now}\n\n'
        f'If you did not request this, please secure your account immediately.\n\n'
        f'— Portfolio CMS'
    )
    html = f'''
<div style="font-family:sans-serif;max-width:520px;margin:auto;padding:2rem;
            border:1px solid #e5e7eb;border-radius:8px;">
  <h2 style="color:#1f2937;margin-top:0;">Password Reset OTP</h2>
  <p>A password reset was requested for your <strong>{role_label}</strong> account.</p>
  <div style="background:#f9fafb;border:1px solid #d1d5db;border-radius:6px;
              padding:1.5rem;text-align:center;margin:1.5rem 0;">
    <span style="font-size:2rem;font-weight:700;letter-spacing:.4rem;color:#4f46e5;">{otp}</span>
  </div>
  <p style="color:#6b7280;font-size:.9rem;">
    Expires in <strong>{ttl_minutes} minutes</strong>. Do not share this code.
  </p>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:1.5rem 0;">
  <p style="color:#9ca3af;font-size:.8rem;">
    Request IP: {ip_address or "unknown"} &bull; Time: {now}
  </p>
</div>'''

    ok, _ = _send_via_resend(recipient_email, subject, text, html=html)
    if ok:
        return True
    logger.warning('Resend OTP send failed; trying SMTP fallback for %s', recipient_email)
    return _smtp_fallback(recipient_email, subject, text)


def send_verification_email(
    recipient_email: str,
    username: str,
    verification_url: str,
) -> bool:
    """Send email verification link."""
    subject = '[Portfolio CMS] Verify Your Email Address'
    text = (
        f'Hello {username},\n\n'
        f'Please verify your email address by clicking the link below:\n\n'
        f'{verification_url}\n\n'
        f'This link expires in 24 hours. If you did not register, ignore this email.\n\n'
        f'— Portfolio CMS'
    )
    html = f'''
<div style="font-family:sans-serif;max-width:520px;margin:auto;padding:2rem;
            border:1px solid #e5e7eb;border-radius:8px;">
  <h2 style="color:#1f2937;margin-top:0;">Verify Your Email</h2>
  <p>Hello <strong>{username}</strong>,</p>
  <p>Click below to verify your email address.</p>
  <a href="{verification_url}"
     style="display:inline-block;background:#4f46e5;color:#fff;padding:.75rem 1.5rem;
            border-radius:6px;text-decoration:none;font-weight:600;margin:1rem 0;">
    Verify Email
  </a>
  <p style="color:#6b7280;font-size:.85rem;">
    Or copy this link: {verification_url}<br>
    Expires in 24 hours.
  </p>
</div>'''
    ok, _ = _send_via_resend(recipient_email, subject, text, html=html)
    if ok:
        return True
    return _smtp_fallback(recipient_email, subject, text)


def send_subscription_email(
    recipient_email: str,
    tenant_name: str,
    event: str,          # 'activated' | 'renewed' | 'expiring_7d' | 'expiring_30d' | 'expiring_3d' | 'expiring_1d' | 'expired'
    plan: str = 'Subscription',
    expires_on: Optional[str] = None,
    days_left: Optional[int] = None,
) -> bool:
    """
    Send subscription lifecycle email.

    event values and when they fire:
      activated   — new subscription activated
      renewed     — subscription renewed
      expiring_30d — 30 days before expiry (yearly plans)
      expiring_7d  — 7 days before expiry (monthly plans)
      expiring_3d  — 3 days before expiry
      expiring_1d  — 1 day before expiry
      expired      — subscription has expired
    """
    event_meta = {
        'activated':    ('Subscription Activated',        '✅ Your subscription is now active.'),
        'renewed':      ('Subscription Renewed',          '🔄 Your subscription has been renewed. Thank you!'),
        'expiring_30d': ('Subscription Expiring in 30 Days', f'⏳ Your {plan} subscription expires in 30 days on {expires_on}.'),
        'expiring_7d':  ('Subscription Expiring Soon',   f'⚠️  Your {plan} subscription expires in 7 days on {expires_on}.'),
        'expiring_3d':  ('Subscription Expiring in 3 Days', f'🚨 Your {plan} subscription expires in 3 days on {expires_on}.'),
        'expiring_1d':  ('Action Required — Expires Tomorrow', f'🚨 Your {plan} subscription expires TOMORROW on {expires_on}.'),
        'expired':      ('Subscription Expired',          f'❌ Your {plan} subscription has expired.'),
    }
    title, summary = event_meta.get(event, ('Subscription Update', 'Your subscription status has changed.'))
    subject = f'[Portfolio CMS] {title}'
    text = (
        f'Hello {tenant_name},\n\n'
        f'{summary}\n\n'
        f'Plan: {plan}\n'
        + (f'Expires: {expires_on}\n' if expires_on else '')
        + (f'Days remaining: {days_left}\n' if days_left is not None else '')
        + f'\nIf you have questions, contact your administrator.\n\n'
          f'— Portfolio CMS'
    )
    ok, _ = _send_via_resend(recipient_email, subject, text)
    if ok:
        return True
    return _smtp_fallback(recipient_email, subject, text)


def send_payment_notification(
    recipient_email: str,
    tenant_name: str,
    status: str,         # 'approved' | 'rejected'
    plan: str = 'Subscription',
    amount: Optional[str] = None,
    reference: Optional[str] = None,
) -> bool:
    """Send payment approved/rejected notification."""
    if status == 'approved':
        subject = '[Portfolio CMS] Payment Approved'
        summary = f'✅ Your payment for {plan} has been approved.'
    else:
        subject = '[Portfolio CMS] Payment Not Approved'
        summary = f'❌ Your payment for {plan} could not be processed.'

    text = (
        f'Hello {tenant_name},\n\n{summary}\n\n'
        + (f'Amount  : {amount}\n' if amount else '')
        + (f'Reference: {reference}\n' if reference else '')
        + f'\n— Portfolio CMS'
    )
    ok, _ = _send_via_resend(recipient_email, subject, text)
    if ok:
        return True
    return _smtp_fallback(recipient_email, subject, text)


def send_system_notification(
    recipient_email: str,
    subject: str,
    message: str,
) -> bool:
    """Generic system notification (superadmin alerts, etc.)."""
    full_subject = f'[Portfolio CMS] {subject}'
    ok, _ = _send_via_resend(recipient_email, full_subject, message)
    if ok:
        return True
    return _smtp_fallback(recipient_email, full_subject, message)

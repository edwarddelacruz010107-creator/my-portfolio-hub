"""
app/services/password_reset_service.py — Password reset orchestration (v3.8)

Three completely isolated reset flows:
  A. Superadmin  — /superadmin/forgot-password
  B. Admin        — /admin/forgot-password       (tenant admin users)
  C. Tenant       — /tenant/forgot-password      (validates against contact_email)

Each flow:
  1. resolve_user()     → look up account, validate email
  2. initiate_reset()   → create OTP record, send email
  3. verify_otp_step()  → verify OTP, return token for password form
  4. complete_reset()   → set new password, destroy all sessions

Session security on password change:
  • session_token rotated → existing sessions invalidated
  • require_password_reset cleared
"""
import logging
import secrets
from datetime import datetime, timezone

from flask import current_app, request as flask_request

from app import db
from app.models import User
from app.models.portfolio import Tenant, GlobalEmailConfig
from app.services.otp_service import create_otp_record, verify_otp
from app.services.email_service import send_otp_email
from app.security import log_security_event

logger = logging.getLogger(__name__)

_MAX_OTP_TTL = 10  # minutes


def _get_ip() -> str:
    fwd = flask_request.headers.get('X-Forwarded-For', '')
    return fwd.split(',')[0].strip() if fwd else (flask_request.remote_addr or 'unknown')


def _get_ua() -> str:
    return (flask_request.headers.get('User-Agent') or '')[:300]


def _recovery_enabled() -> bool:
    try:
        return GlobalEmailConfig.get().recovery_enabled
    except Exception:
        return True  # fail open to avoid locking superadmin out


# ─────────────────────────────────────────────────────────────────────────────
# A. Superadmin reset
# ─────────────────────────────────────────────────────────────────────────────

def initiate_superadmin_reset(submitted_email: str) -> tuple[bool, str]:
    """
    Initiate superadmin OTP reset.
    Returns (sent: bool, masked_email_or_error: str).
    Uses generic message to avoid account enumeration.
    """
    if not _recovery_enabled():
        return False, 'Password recovery is currently disabled.'

    # Always returns generic message regardless of match
    generic = 'If a superadmin account exists with that email, an OTP has been sent.'

    user = User.query.filter_by(is_superadmin=True, email=submitted_email.strip().lower()).first()
    if not user:
        logger.warning('Superadmin reset: email not found — %s (enumeration suppressed)', submitted_email)
        return True, generic  # Lie to prevent enumeration

    ip = _get_ip()
    ua = _get_ua()
    ttl = _get_ttl_minutes()

    raw_otp = create_otp_record(
        user_type='superadmin', user_id=user.id,
        email=user.email, ip_address=ip, user_agent=ua,
    )
    db.session.commit()

    sent = send_otp_email(
        recipient_email=user.email, otp=raw_otp,
        user_type='superadmin', ip_address=ip, user_agent=ua,
        ttl_minutes=ttl,
    )
    if sent:
        log_security_event('sa_pw_reset_initiated', user, f'OTP sent from {ip}', 'info')
    else:
        logger.error('Superadmin OTP email delivery failed for user %s', user.id)

    return True, generic  # Always generic


def verify_superadmin_otp(submitted_email: str, raw_otp: str) -> tuple[bool, str, str | None]:
    """
    Verify OTP, return (ok, message, reset_token).
    reset_token is a short-lived token stored on the User row.
    """
    user = User.query.filter_by(is_superadmin=True, email=submitted_email.strip().lower()).first()
    if not user:
        return False, 'Invalid OTP or account.', None

    ok, msg = verify_otp('superadmin', user.id, raw_otp)
    if not ok:
        log_security_event('sa_otp_failed', user, f'OTP verify failed: {msg}', 'warning')
        return False, msg, None

    token = user.generate_reset_token(expires_in_minutes=15)
    db.session.commit()
    log_security_event('sa_otp_verified', user, 'OTP verified', 'info')
    return True, 'OTP verified. Set your new password.', token


def complete_superadmin_reset(token: str, new_password: str) -> tuple[bool, str]:
    """Apply password, invalidate all sessions."""
    user = User.query.filter_by(is_superadmin=True).filter(
        User.password_reset_token == token
    ).first()
    if not user or not user.verify_reset_token(token):
        return False, 'Reset link is invalid or has expired.'

    _apply_password_change(user, new_password)
    log_security_event('sa_pw_reset_complete', user, f'Password reset from {_get_ip()}', 'info')
    return True, 'Password changed. Please log in.'


# ─────────────────────────────────────────────────────────────────────────────
# B. Admin (tenant admin user) reset
# ─────────────────────────────────────────────────────────────────────────────

def initiate_admin_reset(submitted_email: str) -> tuple[bool, str]:
    """Initiate admin (non-superadmin) OTP reset."""
    if not _recovery_enabled():
        return False, 'Password recovery is currently disabled.'

    generic = 'If an account exists with that email, an OTP has been sent.'
    email   = submitted_email.strip().lower()
    user    = User.query.filter_by(email=email, is_superadmin=False).first()
    if not user:
        return True, generic

    ip, ua, ttl = _get_ip(), _get_ua(), _get_ttl_minutes()
    raw_otp = create_otp_record(
        user_type='admin', user_id=user.id, email=user.email,
        tenant_id=user.tenant_id, ip_address=ip, user_agent=ua,
    )
    db.session.commit()
    send_otp_email(
        recipient_email=user.email, otp=raw_otp,
        user_type='admin', ip_address=ip, user_agent=ua, ttl_minutes=ttl,
    )
    log_security_event('admin_pw_reset_initiated', user, f'OTP sent from {ip}', 'info')
    return True, generic


def verify_admin_otp(submitted_email: str, raw_otp: str) -> tuple[bool, str, str | None]:
    email = submitted_email.strip().lower()
    user  = User.query.filter_by(email=email, is_superadmin=False).first()
    if not user:
        return False, 'Invalid OTP or account.', None

    ok, msg = verify_otp('admin', user.id, raw_otp, tenant_id=user.tenant_id)
    if not ok:
        return False, msg, None

    token = user.generate_reset_token(expires_in_minutes=15)
    db.session.commit()
    return True, 'OTP verified. Set your new password.', token


def complete_admin_reset(token: str, new_password: str) -> tuple[bool, str]:
    user = User.query.filter_by(is_superadmin=False).filter(
        User.password_reset_token == token
    ).first()
    if not user or not user.verify_reset_token(token):
        return False, 'Reset link is invalid or has expired.'
    _apply_password_change(user, new_password)
    log_security_event('admin_pw_reset_complete', user, f'Password reset from {_get_ip()}', 'info')
    return True, 'Password changed. Please log in.'


# ─────────────────────────────────────────────────────────────────────────────
# C. Tenant reset (validates email against Tenant.contact_email)
# ─────────────────────────────────────────────────────────────────────────────

def initiate_tenant_reset(submitted_email: str, username: str | None = None) -> tuple[bool, str]:
    """
    Initiate tenant OTP reset.

    Validation: submitted_email must exactly match the User's email AND
    the tenant's contact_email (both must be set and match).
    If either fails → generic error; no OTP sent; no enumeration.
    """
    if not _recovery_enabled():
        return False, 'Password recovery is currently disabled.'

    generic_ok  = 'If a matching account is found, an OTP has been sent.'
    generic_err = 'Invalid email or account.'

    email = submitted_email.strip().lower()

    query = User.query.filter_by(email=email, is_superadmin=False)
    if username:
        query = query.filter_by(username=username.strip())
    user = query.first()

    if not user:
        return True, generic_ok  # Suppress enumeration

    # CRITICAL: email must match Tenant.contact_email
    tenant = Tenant.query.get(user.tenant_id)
    if not tenant:
        return True, generic_ok

    tenant_contact = (tenant.contact_email or '').strip().lower()
    if not tenant_contact or tenant_contact != email:
        logger.warning(
            'Tenant reset blocked: submitted=%s contact=%s tenant=%s',
            email, tenant_contact, tenant.slug,
        )
        return False, generic_err  # Mismatch — explicit generic error per spec

    ip, ua, ttl = _get_ip(), _get_ua(), _get_ttl_minutes()
    raw_otp = create_otp_record(
        user_type='tenant', user_id=user.id, email=user.email,
        tenant_id=user.tenant_id, ip_address=ip, user_agent=ua,
    )
    db.session.commit()
    send_otp_email(
        recipient_email=email, otp=raw_otp,
        user_type='tenant', ip_address=ip, user_agent=ua, ttl_minutes=ttl,
    )
    log_security_event('tenant_pw_reset_initiated', user, f'OTP sent from {ip}', 'info')
    return True, generic_ok


def verify_tenant_otp(submitted_email: str, raw_otp: str) -> tuple[bool, str, str | None]:
    email = submitted_email.strip().lower()
    user  = User.query.filter_by(email=email, is_superadmin=False).first()
    if not user:
        return False, 'Invalid OTP or account.', None

    # Double-check tenant isolation
    tenant = Tenant.query.get(user.tenant_id)
    if not tenant or (tenant.contact_email or '').strip().lower() != email:
        return False, 'Invalid email or account.', None

    ok, msg = verify_otp('tenant', user.id, raw_otp, tenant_id=user.tenant_id)
    if not ok:
        return False, msg, None

    token = user.generate_reset_token(expires_in_minutes=15)
    db.session.commit()
    return True, 'OTP verified. Set your new password.', token


def complete_tenant_reset(token: str, new_password: str, tenant_id: int) -> tuple[bool, str]:
    """
    Apply password change — enforces tenant isolation via tenant_id.
    """
    user = User.query.filter_by(is_superadmin=False, tenant_id=tenant_id).filter(
        User.password_reset_token == token
    ).first()
    if not user or not user.verify_reset_token(token):
        return False, 'Reset link is invalid or has expired.'
    _apply_password_change(user, new_password)
    log_security_event('tenant_pw_reset_complete', user, f'Password reset from {_get_ip()}', 'info')
    return True, 'Password changed. Please log in.'


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_ttl_minutes() -> int:
    try:
        return max(1, GlobalEmailConfig.get().otp_expiry_minutes or _MAX_OTP_TTL)
    except Exception:
        return _MAX_OTP_TTL


def _apply_password_change(user: User, new_password: str) -> None:
    """
    Set new password, rotate session token (invalidates all existing sessions),
    clear reset token, clear require_password_reset flag.
    """
    user.password                = new_password
    user.session_token           = secrets.token_urlsafe(32)   # Rotate → kills all sessions
    user.require_password_reset  = False
    user.last_password_changed   = datetime.now(timezone.utc)
    user.clear_reset_token()
    db.session.commit()
    logger.info('Password changed for user %s (type detection at call site)', user.id)

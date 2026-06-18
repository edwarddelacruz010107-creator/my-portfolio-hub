"""
app/services/email_service.py — Centralised email dispatch (v5.0)

MIGRATION v5.0:
  • Flask-Mail / SMTP REMOVED entirely.
  • MailerSend is the single transactional email provider.
  • Basin handles tenant contact forms (see basin_service.py).

All email now routes through mailersend_service.
This module re-exports those functions for backward compatibility.
"""
import logging

logger = logging.getLogger(__name__)

# ── Re-export MailerSend functions as the canonical email interface ────────────
from app.services.mailersend_service import (          # noqa: F401  (re-export)
    send_email,
    send_otp_email,
    send_verification_email,
    send_subscription_email,
    send_payment_notification,
    send_system_notification,
    validate_mailersend_key,
)


# ── Deprecated shims — retained for import compatibility only ─────────────────

def validate_resend_key(key: str) -> tuple[bool, str]:
    """Deprecated. Resend removed in v5.0. Use validate_mailersend_key()."""
    logger.warning('validate_resend_key() called — Resend removed in v5.0.')
    return False, 'Resend is no longer used. Configure MAILERSEND_API_KEY instead.'


def validate_web3forms_key(key: str) -> tuple[bool, str]:
    """Deprecated. Web3Forms removed in v4.1."""
    logger.warning('validate_web3forms_key() called — Web3Forms is deprecated.')
    return False, 'Web3Forms is no longer used. Configure MAILERSEND_API_KEY instead.'


def send_contact_form_web3forms(*args, **kwargs) -> bool:
    """Deprecated shim. Route contact forms through basin_service or internal Inquiry."""
    logger.warning('send_contact_form_web3forms() called — Web3Forms is deprecated.')
    return False

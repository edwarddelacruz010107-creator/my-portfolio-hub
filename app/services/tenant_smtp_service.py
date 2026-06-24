"""
app/services/tenant_smtp_service.py — Per-tenant SMTP resolution & dispatch (v5.9)

ISOLATION CONTRACT:
    - tenant_id is ALWAYS an explicit argument from the caller's own request
      context. This module never infers "current tenant" from a global —
      that ambiguity is exactly how Tenant A's email could end up using
      Tenant B's credentials. Callers (routes) are responsible for passing
      the tenant_id that belongs to the authenticated session.
    - Reuses smtp_service.py's low-level MIME/transport helpers (already
      hardened: TLS-enforced, 30s timeout, bounded transient retry) so the
      wire-level send logic isn't duplicated or re-implemented with subtly
      different behavior. smtp_service.py itself is NOT modified — its
      superadmin isolation guarantee (env-var only config, zero DB reads)
      is untouched.
    - If a tenant has no active TenantSmtpCredential row, this module falls
      back to the platform's global SMTP env config (smtp_service.py),
      never to another tenant's row.

NOT YET IMPLEMENTED (see hardening-pass notes):
    - Async dispatch (Celery/RQ). Calls here are synchronous; the caller's
      request thread blocks for the duration of the SMTP transaction
      (bounded by _DEFAULT_TIMEOUT * (1 + _MAX_RETRIES) worst case).
    - Persistent delivery-metrics table / dashboard. last_test_* columns on
      TenantSmtpCredential cover connectivity checks; per-send metrics
      still only go to the application log, not a queryable table.
"""
from __future__ import annotations

import logging
import time
import smtplib
import ssl
from typing import Optional

from app.services import smtp_service as _global_smtp

logger = logging.getLogger(__name__)

# ── Minimal per-tenant outbound rate guard ──────────────────────────────────
# In-process only — resets per worker, not shared across dynos. Good enough
# to blunt an accidental loop or a single compromised tenant hammering their
# own SMTP relay; NOT a substitute for a shared (Redis-backed) limiter under
# multi-worker production load. Flagged explicitly rather than presented as
# a complete solution.
_RATE_WINDOW_SECONDS = 60
_RATE_MAX_PER_WINDOW = 30
_send_log: dict[int, list[float]] = {}


def _rate_limited(tenant_id: int) -> bool:
    now = time.monotonic()
    window_start = now - _RATE_WINDOW_SECONDS
    history = [t for t in _send_log.get(tenant_id, []) if t >= window_start]
    _send_log[tenant_id] = history
    if len(history) >= _RATE_MAX_PER_WINDOW:
        return True
    history.append(now)
    return False


def resolve_tenant_smtp_config(tenant_id: int) -> Optional[dict]:
    """
    Return a smtp_service-shaped cfg dict for this tenant's own SMTP if
    active and fully configured, else None (caller should fall back).
    """
    from app.models.tenant_smtp_credential import TenantSmtpCredential

    cred = TenantSmtpCredential.for_tenant(tenant_id)
    if not cred or not cred.is_active:
        return None

    return {
        'host':       cred.host,
        'port':       cred.port,
        'username':   cred.username,
        'password':   cred.password,        # decrypted here only, in-process
        'from_email': cred.from_email,
        'from_name':  cred.from_name or 'Portfolio CMS',
        'timeout':    _global_smtp._DEFAULT_TIMEOUT,
    }


def send_tenant_email(
    *,
    tenant_id: int,
    to: str,
    subject: str,
    text: str,
    html: Optional[str] = None,
    to_name: Optional[str] = None,
    reply_to: Optional[str] = None,
    context: str = 'tenant_smtp',
) -> tuple[bool, str, str]:
    """
    Send via this tenant's own SMTP if configured+enabled, otherwise fall
    back to the global SMTP env config (smtp_service.py).

    Returns (success, message, provider_used) where provider_used is
    'tenant_smtp' or 'global_smtp_fallback' — log/store this for delivery
    observability.
    """
    if _rate_limited(tenant_id):
        logger.warning(
            'tenant_smtp.send [%s]: tenant_id=%s RATE LIMITED (%d/%ds) — message NOT sent',
            context, tenant_id, _RATE_MAX_PER_WINDOW, _RATE_WINDOW_SECONDS,
        )
        return False, 'tenant SMTP rate limit exceeded — try again shortly', 'rate_limited'

    cfg = resolve_tenant_smtp_config(tenant_id)

    if cfg is None:
        logger.info(
            'tenant_smtp.send [%s]: tenant_id=%s no active tenant SMTP → global fallback',
            context, tenant_id,
        )
        ok, msg = _global_smtp.send_email(
            to=to, subject=subject, text=text, html=html,
            to_name=to_name, reply_to=reply_to,
            context=f'{context}:fallback:tenant={tenant_id}',
        )
        return ok, msg, 'global_smtp_fallback'

    if not _global_smtp._is_configured(cfg):
        logger.error(
            'tenant_smtp.send [%s]: tenant_id=%s tenant SMTP row incomplete despite is_active=True '
            '(data inconsistency) → global fallback',
            context, tenant_id,
        )
        ok, msg = _global_smtp.send_email(
            to=to, subject=subject, text=text, html=html,
            to_name=to_name, reply_to=reply_to,
            context=f'{context}:fallback:tenant={tenant_id}',
        )
        return ok, msg, 'global_smtp_fallback'

    last_err = 'unknown error'
    max_retries = _global_smtp._MAX_RETRIES
    for attempt in range(1, max_retries + 2):
        t0 = time.monotonic()
        try:
            mime_msg = _global_smtp._build_message(
                to, subject, text, html, cfg, to_name=to_name, reply_to=reply_to,
            )
            _global_smtp._dispatch(cfg, mime_msg, to)
            elapsed = (time.monotonic() - t0) * 1000
            logger.info(
                'tenant_smtp.send [%s]: tenant_id=%s delivered to=%s attempt=%d/%d latency=%.0fms',
                context, tenant_id, to, attempt, max_retries + 1, elapsed,
            )
            return True, 'delivered', 'tenant_smtp'
        except Exception as exc:  # noqa: BLE001 — classified, never leaks credentials
            transient, safe_msg = _global_smtp._classify_smtp_error(exc)
            last_err = safe_msg
            logger.warning(
                'tenant_smtp.send [%s]: tenant_id=%s attempt %d/%d failed reason=%s transient=%s',
                context, tenant_id, attempt, max_retries + 1, safe_msg, transient,
            )
            if not transient or attempt > max_retries:
                break
            time.sleep(_global_smtp._RETRY_BACKOFF * attempt)

    logger.error(
        'tenant_smtp.send [%s]: tenant_id=%s ALL ATTEMPTS FAILED last_error=%s → global fallback',
        context, tenant_id, last_err,
    )
    ok, msg = _global_smtp.send_email(
        to=to, subject=subject, text=text, html=html,
        to_name=to_name, reply_to=reply_to,
        context=f'{context}:fallback_after_tenant_failure:tenant={tenant_id}',
    )
    return ok, msg, ('global_smtp_fallback' if ok else 'failed')


def test_tenant_smtp_connection(tenant_id: int) -> tuple[bool, str]:
    """
    Diagnostics/validation endpoint support: attempt a connect+login only
    (no message sent). Records the result on the credential row so the
    admin UI can show last-known status without re-testing on every page
    load.
    """
    from app import db
    from app.models.tenant_smtp_credential import TenantSmtpCredential
    from datetime import datetime, timezone

    cred = TenantSmtpCredential.for_tenant(tenant_id)
    if not cred:
        return False, 'No SMTP credential configured for this tenant.'
    if not cred.is_configured:
        return False, 'SMTP credential is missing required fields (host/username/password/from_email).'

    ok = True
    err = ''
    try:
        if cred.port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(cred.host, cred.port, timeout=_global_smtp._DEFAULT_TIMEOUT, context=context) as server:
                server.login(cred.username, cred.password)
        else:
            with smtplib.SMTP(cred.host, cred.port, timeout=_global_smtp._DEFAULT_TIMEOUT) as server:
                server.ehlo()
                if cred.use_tls:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                server.login(cred.username, cred.password)
    except Exception as exc:  # noqa: BLE001
        ok = False
        _, err = _global_smtp._classify_smtp_error(exc)

    cred.last_test_at = datetime.now(timezone.utc)
    cred.last_test_ok = ok
    cred.last_test_error = err[:500] if err else None
    db.session.commit()

    logger.info(
        'tenant_smtp.test: tenant_id=%s ok=%s err=%s',
        tenant_id, ok, err or '-',
    )
    return ok, ('Connection successful.' if ok else err)

"""
app/models/tenant_smtp_credential.py — Per-tenant SMTP credential storage (v5.9)

ARCHITECTURAL ROLE
───────────────────────────────────────────────────────────────────────────
Optional, isolated per-tenant SMTP configuration. A tenant can supply their
own SMTP_HOST/PORT/USERNAME/PASSWORD/FROM_EMAIL/FROM_NAME/use_tls instead of
relying on the platform's shared MailerSend/Web3Forms/Basin providers.

ISOLATION CONTRACT:
    - One row per tenant (unique constraint on tenant_id).
    - Reading/writing this table NEVER takes a tenant_id from anything other
      than the authenticated tenant's own session/context. Callers must pass
      tenant_id explicitly — there is no "current tenant" magic here, by
      design, so a copy-paste mistake can't leak Tenant A's row into a
      request scoped to Tenant B.
    - Resolution and sending happens in app.services.tenant_smtp_service,
      which is the ONLY place this model's decrypted password should be
      read into memory. It must never be passed to a template, included in
      an API response, or logged.

ENCRYPTION:
    - Password is Fernet-encrypted via app.models.core.encrypt_secret /
      decrypt_secret — the same helper already used for TenantFormSettings
      API keys. Supports key rotation via FERNET_KEY_PREVIOUS (see core.py).
    - The plaintext password setter never logs the value. The getter
      decrypts on access only; nothing is cached in plaintext on the
      instance.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app import db
from app.models.core import encrypt_secret, decrypt_secret

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TenantSmtpCredential(db.Model):
    """
    Per-tenant SMTP transport configuration.

    is_enabled=False (default) means: ignore this row, use the platform's
    shared provider / global SMTP fallback. A tenant's SMTP only takes
    effect when explicitly enabled AND fully configured.
    """
    __tablename__ = 'tenant_smtp_credentials'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', name='uq_tenant_smtp_credentials'),
        db.Index('ix_tsc_is_enabled', 'is_enabled'),
    )

    id        = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer,
        db.ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )

    host        = db.Column(db.String(255), nullable=True)
    port        = db.Column(db.Integer, nullable=False, default=587)
    username    = db.Column(db.String(255), nullable=True)

    # Fernet-encrypted — NEVER expose raw value to frontend or logs
    _password_encrypted = db.Column('password_encrypted', db.Text, nullable=False, default='')

    from_email  = db.Column(db.String(200), nullable=True)
    from_name   = db.Column(db.String(200), nullable=True, default='Portfolio CMS')
    use_tls     = db.Column(db.Boolean, nullable=False, default=True)   # STARTTLS (587) vs implicit TLS (465)

    is_enabled  = db.Column(db.Boolean, nullable=False, default=False)

    # Lightweight last-known-state tracking (not a full delivery log — see
    # app.services.tenant_smtp_service for per-send logging).
    last_test_at      = db.Column(db.DateTime(timezone=True), nullable=True)
    last_test_ok      = db.Column(db.Boolean, nullable=True)
    last_test_error   = db.Column(db.String(500), nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    tenant = db.relationship(
        'Tenant',
        backref=db.backref(
            'smtp_credential',
            uselist=False,
            cascade='all, delete-orphan',
            passive_deletes=True,
        ),
    )

    # ── Encrypted password accessors ──────────────────────────────────────────

    @property
    def password(self) -> str:
        """Decrypt password. Returns '' on failure — never raises."""
        return decrypt_secret(self._password_encrypted)

    @password.setter
    def password(self, value: str) -> None:
        if not value:
            self._password_encrypted = ''
            return
        encrypted = encrypt_secret(value)
        if not encrypted:
            raise RuntimeError(
                'encrypt_secret() returned empty — FERNET_KEY may be misconfigured. '
                'Check your environment and ensure FERNET_KEY is set correctly.'
            )
        self._password_encrypted = encrypted

    @property
    def password_masked(self) -> str:
        """Masked display for the admin UI — never more than last 4 chars."""
        raw = self.password
        if not raw:
            return ''
        visible = raw[-4:] if len(raw) >= 4 else raw
        return f'{"*" * 11}{visible}'

    # ── Status helpers ────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        """True if every field required to attempt a send is present."""
        return bool(
            self.host and self.username and self._password_encrypted
            and self.from_email and self.port
        )

    @property
    def is_active(self) -> bool:
        """True if this tenant's own SMTP should be used (enabled + configured)."""
        return bool(self.is_enabled and self.is_configured)

    # ── Lookup helpers ────────────────────────────────────────────────────────

    @classmethod
    def get_or_create(cls, tenant_id: int) -> 'TenantSmtpCredential':
        obj = cls.query.filter_by(tenant_id=tenant_id).first()
        if not obj:
            obj = cls(tenant_id=tenant_id, is_enabled=False, port=587, use_tls=True)
            db.session.add(obj)
            db.session.flush()
        return obj

    @classmethod
    def for_tenant(cls, tenant_id: int) -> Optional['TenantSmtpCredential']:
        """Nullable lookup — returns None if no row exists for this tenant."""
        return cls.query.filter_by(tenant_id=tenant_id).first()

    def __repr__(self) -> str:
        return (
            f'<TenantSmtpCredential tenant_id={self.tenant_id} '
            f'host={self.host!r} active={self.is_active}>'
        )

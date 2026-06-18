"""
wsgi.py — Gunicorn / uWSGI entry point for production.

Usage:
  gunicorn wsgi:app
  gunicorn --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 2 wsgi:app
"""
import os
from app import create_app
from werkzeug.middleware.proxy_fix import ProxyFix

app = create_app(os.environ.get('FLASK_ENV', 'production'))

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

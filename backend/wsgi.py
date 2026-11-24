"""WSGI entry point: ``gunicorn wsgi:app`` or ``flask --app wsgi run``."""

from ymer import create_app

app = create_app()

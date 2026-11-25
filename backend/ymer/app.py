"""Flask application factory."""

from typing import Any

from flask import Flask, jsonify, request
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from ymer.config import Settings, get_settings
from ymer.db import init_db
from ymer.extensions import init_redis, init_s3


def create_app(overrides: dict[str, Any] | None = None) -> Flask:
    """Build the app. ``overrides`` replaces individual settings (used by tests)."""
    settings = get_settings()
    if overrides:
        settings = settings.model_copy(update=overrides)
    if settings.env == "production" and (
        settings.secret_key == "change-me" or len(settings.secret_key) < 32
    ):
        raise RuntimeError("Set YMER_SECRET_KEY to a random value of at least 32 characters.")
    if settings.env == "production" and (
        settings.collab_secret == "change-me-collab" or len(settings.collab_secret) < 32
    ):
        raise RuntimeError("Set YMER_COLLAB_SECRET to a random value of at least 32 characters.")

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=settings.secret_key,
        TESTING=settings.env == "test",
        JSON_SORT_KEYS=False,
        # Multipart overhead on top of the largest file we accept.
        MAX_CONTENT_LENGTH=settings.max_upload_bytes + 1024 * 1024,
    )
    app.extensions["ymer_settings"] = settings
    if settings.trusted_proxies > 0:
        n = settings.trusted_proxies
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)  # type: ignore[method-assign]

    database_url = settings.test_database_url if settings.env == "test" else settings.database_url
    init_db(app, database_url, echo=settings.database_echo)
    init_redis(app, settings)
    init_s3(app, settings)

    from ymer.ai import init_ai

    init_ai(app, settings)

    CORS(
        app,
        resources={r"/api/*": {"origins": settings.cors_origins}},
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
    )

    from ymer.api import api_bp

    app.register_blueprint(api_bp)

    from ymer.internal import internal_bp

    app.register_blueprint(internal_bp)

    from ymer.cli import register_cli

    register_cli(app)

    @app.errorhandler(HTTPException)
    def _http_error(err: HTTPException):
        body = {"error": err.name, "message": err.description}
        if errors := getattr(err, "errors", None):
            body["errors"] = errors
        response = jsonify(body)
        response.status_code = err.code or 500
        for name, value in err.get_headers():  # e.g. Retry-After on 429
            if name.lower() != "content-type":
                response.headers[name] = value
        return response

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if request.path.startswith("/api/") and response.mimetype == "application/json":
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    return app


def settings_for(app: Flask) -> Settings:
    return app.extensions["ymer_settings"]

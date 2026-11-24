"""HTTP API. All routes live under ``/api``; add feature blueprints here."""

from flask import Blueprint

from ymer.api.auth import auth_bp
from ymer.api.health import health_bp
from ymer.api.pages import pages_bp
from ymer.api.workspaces import workspaces_bp

api_bp = Blueprint("api", __name__, url_prefix="/api")
api_bp.register_blueprint(health_bp)
api_bp.register_blueprint(auth_bp)
api_bp.register_blueprint(workspaces_bp)
api_bp.register_blueprint(pages_bp)

__all__ = ["api_bp"]

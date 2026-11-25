"""HTTP API. All routes live under ``/api``; add feature blueprints here."""

from flask import Blueprint

from ymer.api.ai import ai_bp, ai_status_bp
from ymer.api.ai_settings import bp as ai_settings_bp
from ymer.api.auth import auth_bp
from ymer.api.databases import databases_bp
from ymer.api.documents import documents_bp
from ymer.api.factory import factory_bp
from ymer.api.factory_changes import budget_bp, changes_bp
from ymer.api.factory_deliver import deliver_bp, preview_bp
from ymer.api.files import files_bp
from ymer.api.health import health_bp
from ymer.api.pages import pages_bp
from ymer.api.search import search_bp
from ymer.api.workspaces import workspaces_bp

api_bp = Blueprint("api", __name__, url_prefix="/api")
api_bp.register_blueprint(health_bp)
api_bp.register_blueprint(auth_bp)
api_bp.register_blueprint(workspaces_bp)
api_bp.register_blueprint(pages_bp)
api_bp.register_blueprint(documents_bp)
api_bp.register_blueprint(files_bp)
api_bp.register_blueprint(databases_bp)
api_bp.register_blueprint(search_bp)
api_bp.register_blueprint(ai_status_bp)
api_bp.register_blueprint(ai_settings_bp)
api_bp.register_blueprint(ai_bp)
api_bp.register_blueprint(factory_bp)
api_bp.register_blueprint(deliver_bp)
api_bp.register_blueprint(preview_bp)
api_bp.register_blueprint(changes_bp)
api_bp.register_blueprint(budget_bp)

__all__ = ["api_bp"]

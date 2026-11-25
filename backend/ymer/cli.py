"""Flask CLI commands: ``flask --app wsgi <command>``."""

import click
from botocore.exceptions import ClientError
from flask import Flask, current_app

from ymer.app import settings_for
from ymer.extensions import get_s3


def register_cli(app: Flask) -> None:
    @app.cli.command("init-storage")
    def init_storage() -> None:
        """Create the S3 bucket named by YMER_S3_BUCKET if it does not exist."""
        bucket = settings_for(current_app).s3_bucket
        s3 = get_s3()
        try:
            s3.head_bucket(Bucket=bucket)
            click.echo(f"Bucket '{bucket}' already exists.")
        except ClientError:
            s3.create_bucket(Bucket=bucket)
            click.echo(f"Created bucket '{bucket}'.")

    @app.cli.command("ai-worker")
    @click.option("--once", is_flag=True, help="Drain the queue once and exit.")
    @click.option("--interval", default=2.0, show_default=True, help="Seconds between polls.")
    def ai_worker(once: bool, interval: float) -> None:
        """Embed pages queued for semantic search (ai_index_queue)."""
        import time

        from ymer.ai import get_ai
        from ymer.ai.indexing import process_queue
        from ymer.db import db_session

        embedder = get_ai().embedder
        if embedder is None:
            raise click.ClickException("Embeddings are off (YMER_AI_EMBEDDING_PROVIDER=off).")
        click.echo(f"AI worker started ({embedder.name}: {embedder.model}).")
        while True:
            done = process_queue(db_session, embedder)
            if done:
                click.echo(f"Indexed {done} page(s).")
            db_session.remove()
            if once and not done:
                return
            if not done:
                time.sleep(interval)

    @app.cli.command("factory-worker")
    @click.option("--once", is_flag=True, help="Run the queued builds, then exit.")
    @click.option("--interval", default=2.0, show_default=True, help="Seconds between polls.")
    def factory_worker(once: bool, interval: float) -> None:
        """Run queued Factory builds, one at a time (start several workers for more)."""
        import time

        from flask import current_app

        from ymer.factory.runner import sandbox_for, work_once

        flask_app = current_app._get_current_object()
        sandbox = sandbox_for(flask_app)
        if sandbox is None:
            raise click.ClickException("Builds are off (YMER_FACTORY_SANDBOX=off).")
        click.echo(f"Factory worker started (sandbox: {sandbox.name}).")
        while True:
            worked = work_once(flask_app)
            if once and not worked:
                return
            if not worked:
                time.sleep(interval)

    @app.cli.command("ai-reindex")
    @click.option("--workspace", "workspace_id", default=None, help="Only this workspace id.")
    def ai_reindex(workspace_id: str | None) -> None:
        """Queue every page for re-embedding (e.g. after changing the embedding model)."""
        import uuid

        from ymer.ai.indexing import enqueue_workspace
        from ymer.db import db_session

        count = enqueue_workspace(db_session, uuid.UUID(workspace_id) if workspace_id else None)
        db_session.commit()
        click.echo(f"Queued {count} page(s). Run `flask --app wsgi ai-worker` to index them.")

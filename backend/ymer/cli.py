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

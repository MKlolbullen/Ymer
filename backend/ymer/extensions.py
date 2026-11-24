"""Clients for external services (Redis, S3), created once per app."""

import boto3
import redis
from botocore.config import Config as BotoConfig
from flask import Flask, current_app

from ymer.config import Settings


def init_redis(app: Flask, settings: Settings) -> None:
    app.extensions["redis"] = redis.Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.health_check_timeout,
        socket_timeout=settings.health_check_timeout,
        decode_responses=True,
    )


def init_s3(app: Flask, settings: Settings) -> None:
    app.extensions["s3"] = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=settings.health_check_timeout,
            read_timeout=settings.health_check_timeout,
            retries={"max_attempts": 1},
        ),
    )


def get_redis() -> redis.Redis:
    return current_app.extensions["redis"]


def get_s3():
    return current_app.extensions["s3"]

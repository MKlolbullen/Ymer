# Ymer API, AI worker and Factory worker (one image, different commands).
# Build from the repository root: docker build -f packaging/docker/api.Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git: Factory delivery pushes releases to GitHub/GitLab over HTTPS.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --create-home --home-dir /home/ymer --uid 10001 ymer

WORKDIR /app
# The Modal sandbox builds its images from infra/factory, found relative to the source.
COPY infra/factory infra/factory
COPY backend/pyproject.toml backend/
COPY backend/ymer backend/ymer
RUN pip install -e "./backend[server,modal]"
COPY backend/alembic.ini backend/wsgi.py backend/
COPY backend/migrations backend/migrations
COPY VERSION ./

USER ymer
WORKDIR /app/backend
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--worker-class", "gthread", "--workers", "2", \
     "--threads", "8", "--timeout", "300", "--forwarded-allow-ips", "*", "--access-logfile", "-", \
     "wsgi:app"]

LABEL org.opencontainers.image.source="https://github.com/MKlolbullen/Ymer" \
      org.opencontainers.image.title="Ymer API and workers"

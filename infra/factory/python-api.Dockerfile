# Sandbox image for the "python-api" stack: FastAPI and its test client, preinstalled
# because builds run without network.
FROM python:3.12-slim
RUN pip install --no-cache-dir fastapi==0.115.6 httpx==0.28.1 sqlalchemy==2.0.36 pytest==8.3.3
WORKDIR /work

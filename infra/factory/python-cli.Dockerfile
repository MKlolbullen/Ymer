# Sandbox image for the "python-cli" stack.
FROM python:3.12-slim
RUN pip install --no-cache-dir pytest==8.3.3 ruff==0.8.4
WORKDIR /work

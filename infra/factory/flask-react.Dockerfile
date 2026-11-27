# Sandbox image for the "flask-react" stack: the backend's Python packages, preinstalled
# because builds run without network.
FROM python:3.12-slim
RUN pip install --no-cache-dir flask==3.0.3 sqlalchemy==2.0.36 pytest==8.3.3 ruff==0.8.4
WORKDIR /work

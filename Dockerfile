# Written by Ymer Factory. Build with: docker build -t app .
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .
ENTRYPOINT ["python", "-m", "app.cli"]

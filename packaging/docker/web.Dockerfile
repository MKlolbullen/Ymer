# Ymer web front: the built React app behind nginx, which also proxies /api to the API,
# /collab to the collaboration server and /ymer-files/ to object storage, so the
# browser talks to a single origin.
# Build from the repository root: docker build -f packaging/docker/web.Dockerfile .
# Static output: build once on the runner's own platform for every target platform.
FROM --platform=$BUILDPLATFORM node:22-slim AS build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY packaging/docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
EXPOSE 8080

LABEL org.opencontainers.image.source="https://github.com/MKlolbullen/Ymer" \
      org.opencontainers.image.title="Ymer web front"

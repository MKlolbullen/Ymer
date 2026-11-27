# Ymer realtime collaboration server (Hocuspocus). Node runs the TypeScript directly.
# Build from the repository root: docker build -f packaging/docker/collab.Dockerfile .
FROM node:22-slim

ENV NODE_ENV=production
WORKDIR /app/collab
COPY collab/package.json collab/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY collab/src src

USER node
EXPOSE 1234
CMD ["node", "src/main.ts"]

LABEL org.opencontainers.image.source="https://github.com/MKlolbullen/Ymer" \
      org.opencontainers.image.title="Ymer collaboration server"

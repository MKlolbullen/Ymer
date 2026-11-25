# Sandbox image for the "node-cli" stack: Node for `node --test`, with no npm packages
# (builds have no network, so the app uses Node's standard library only).
FROM node:22-slim
WORKDIR /work

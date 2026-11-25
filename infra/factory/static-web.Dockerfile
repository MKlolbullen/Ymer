# Sandbox image for the "static-web" stack: Node for `node --test`. No packages are
# installed at run time (builds have no network), so everything a test needs is here.
FROM node:22-slim
WORKDIR /work

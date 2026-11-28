# Ymer server bundle

Runs the whole of Ymer on one machine with Docker Compose: the web app, the API,
the realtime collaboration server, the AI and Factory workers, PostgreSQL (with
pgvector), Redis and MinIO. Only the web front is published, on port 8080.

## Requirements

Docker with the Compose v2 plugin: Docker Engine on Linux, Docker Desktop on
Windows and macOS. About 2 GB of disk for the images.

## Run it

Linux and macOS:

```sh
./ymerctl start     # creates ymer.env with fresh secrets, then starts everything
./ymerctl status
./ymerctl stop      # data is kept in Docker volumes
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File .\ymerctl.ps1 start
```

Then open http://localhost:8080 and create an account.

## Settings

`ymerctl init` (run by `start`) writes `ymer.env` next to `compose.yaml` with
random secrets. Edit it and run `ymerctl restart`:

- `YMER_PUBLIC_URL`: the address people open. Downloads are signed for it, so it
  must be the name or IP they really use.
- `YMER_HTTP_BIND=0.0.0.0`: reachable from other machines (default: this machine only).
- `YMER_COOKIE_SECURE=true` and `YMER_TRUSTED_PROXIES=2` when your own HTTPS reverse
  proxy sits in front.
- AI (`YMER_AI_PROVIDER`, `ANTHROPIC_API_KEY`, ...) and Factory (`YMER_FACTORY_SANDBOX`)
  settings, listed in the file. Any `YMER_*` setting from the project's DEVELOPMENT.md works.

## Production

`ymerctl https ymer.example.com you@example.com` serves Ymer on that name with an
automatic certificate. `ymerctl backup` / `ymerctl restore <file>` back up and restore
the database and files, and `YMER_MONITORING=true` adds Prometheus, alerts and Grafana.
`ymerctl check` says what is still missing. Full guide:
[docs/production.md](https://github.com/MKlolbullen/Ymer/blob/main/docs/production.md).
These commands are in the Linux `ymerctl`; on Windows use WSL for them.

## Update

Download the newer bundle, copy your `ymer.env` into it, then run
`ymerctl update`. It takes a backup first; migrations run on start.

## Remove

`ymerctl stop`, then `ymerctl compose down -v` deletes the containers **and all data**.

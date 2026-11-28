# Installing Ymer

To use Ymer on your own machine, install a **server** and open it in your browser. If someone already hosts Ymer for you, open their server URL or install the optional desktop client and enter that URL on first launch.

| What you want | What to download or run | Where to start |
| --- | --- | --- |
| Run Ymer on a Windows PC | `Ymer-Setup-<version>.exe` | [Windows](#windows) |
| Run a Debian or Ubuntu server that starts at boot | `ymer_<version>_all.deb` | [Linux with systemd](#linux-with-systemd) |
| Run the full stack from a folder on Linux or Windows | `ymer-server-<version>.tar.gz` or `.zip` | [Portable Docker bundle](#portable-docker-bundle) |
| Edit Ymer and see changes locally | Git checkout and `./install.sh` | [Development from source](#development-from-source) |
| Connect a desktop app to an existing server | `Ymer-Desktop-*` for your OS | [Release downloads](https://github.com/MKlolbullen/Ymer/releases) |
| Use Ymer on an Android phone or iPhone | `Ymer-Android-<version>.apk` or `Ymer-iOS-<version>.ipa` | [Phone apps](DEVELOPMENT.md#phone-apps-ios-and-android) |

Get the server installer or archive from **Assets** on the release you want. The automatic “Source code” archives contain the repository. If a release has no server assets yet, use the development instructions below. [Ymer releases](https://github.com/MKlolbullen/Ymer/releases)

## Windows

1. Install Docker Desktop and select the **WSL 2** engine and **Linux containers**. If WSL is missing, open PowerShell **as Administrator**, run `wsl --install`, and restart Windows if prompted. Start Docker Desktop and wait for its engine to be ready. [Microsoft WSL setup](https://learn.microsoft.com/en-us/windows/wsl/install) · [Docker Desktop WSL setup](https://docs.docker.com/desktop/features/wsl/)
2. In an ordinary PowerShell window, run the checks below. `docker info` should show a **Server** section. Use a current Compose plugin that supports `--wait` and the bundle’s required environment file.
3. Run `Ymer-Setup-<version>.exe` for your Windows account. In the Start menu, choose **Ymer → Start Ymer**. The first launch downloads the images, creates storage, applies database migrations, and opens the browser.
4. Open **http://localhost:8080** and create your account. **Stop Ymer** stops the stack and keeps the data. **Ymer settings** opens the configuration. Stop and start Ymer after editing it.

```powershell
docker info
docker compose version
```

For logs from the default installation directory, run this in PowerShell. Press Ctrl+C to leave the log view.

```powershell
Set-Location "$env:LOCALAPPDATA\Programs\Ymer"
powershell -NoProfile -ExecutionPolicy Bypass -File .\ymerctl.ps1 logs api
```

The execution-policy option applies to that PowerShell invocation. The Start menu shortcuts also invoke the bundled script this way.

## Linux with systemd

On Debian or Ubuntu, install Docker Engine and its Compose plugin, start Docker, and check daemon access. Use Docker’s instructions for your distribution. [Debian](https://docs.docker.com/engine/install/debian/) · [Ubuntu](https://docs.docker.com/engine/install/ubuntu/)

```bash
sudo systemctl enable --now docker
sudo docker info
sudo docker compose version
```

Download the server `.deb` to an empty directory. From that directory run:

```bash
sudo apt install ./ymer_*_all.deb
sudo systemctl status ymer --no-pager
sudo ymerctl status
sudo ymerctl url
```

The package enables and starts `ymer.service`. **On first installation it binds to all interfaces** and prints a URL based on the host name and port 8080. Open the printed URL and create an account. For a workstation that should listen only on loopback from first launch, use the portable bundle below. To change an existing package installation, edit `/etc/ymer/ymer.env` with `sudoedit`, then run `sudo systemctl restart ymer`.

Manage the service with `sudo systemctl stop ymer` and `sudo systemctl start ymer`. Use `sudo ymerctl logs api` for app logs or `sudo journalctl -u ymer -n 100 --no-pager` for startup failures.

## Portable Docker bundle

Use this path for a local installation without systemd, or when you prefer an unpacked folder on Windows. Install Docker Engine with Compose on Linux, or start Docker Desktop on Windows. The release images contain the Python and Node runtimes.

**Linux.** Download the server tarball to an empty directory. Run these commands there with an account that can access the Docker daemon:

```bash
tar -xzf ymer-server-*.tar.gz
cd ymer-server-*/
./ymerctl start
./ymerctl status
./ymerctl url
```

**Windows.** Extract the server ZIP using **Extract All**. Open PowerShell in the extracted directory containing `compose.yaml` and `ymerctl.ps1`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ymerctl.ps1 start
powershell -NoProfile -ExecutionPolicy Bypass -File .\ymerctl.ps1 status
```

**First login.** Open **http://localhost:8080**, register, and create a workspace. To check readiness directly, open **http://localhost:8080/api/health/ready**. The bundle publishes one loopback port. Its web server routes browser traffic to the API, collaboration service, and file store. PostgreSQL, Redis, MinIO, and both workers run inside Compose. An exited `setup` container with exit code 0 is expected after it finishes migrations and bucket creation.

### Docker commands for everyday use

From the bundle directory on Linux:

| Task | Command |
| --- | --- |
| Create `ymer.env` before the first start, to customize the port or URL | `./ymerctl init` |
| List containers, including the one-time setup job | `./ymerctl status` |
| Follow API logs or inspect a failed migration | `./ymerctl logs api` or `./ymerctl compose logs setup` |
| Stop while keeping settings and data | `./ymerctl stop` |
| Start again or apply configuration changes | `./ymerctl start` or `./ymerctl restart` |
| Pass a command through to the bundle’s Docker Compose project | `./ymerctl compose ps --all` |

On Windows, substitute `powershell -NoProfile -ExecutionPolicy Bypass -File .\ymerctl.ps1` for `./ymerctl`. On the Debian package, use `sudo ymerctl` for inspection and `systemctl` for the service lifecycle.

### Settings, data, and upgrades

The first start creates `ymer.env` beside `compose.yaml` with random secrets. The Debian package uses `/etc/ymer/ymer.env`. For a different local port, set **both** `YMER_HTTP_PORT=8081` and `YMER_PUBLIC_URL=http://localhost:8081`, then restart. Use the address people actually open for `YMER_PUBLIC_URL` because Ymer uses it for signed file downloads.

For access through your own HTTPS reverse proxy on the same host, keep the bundle on loopback, forward HTTP and WebSocket traffic to it, and set `YMER_PUBLIC_URL` to the external HTTPS origin and `YMER_COOKIE_SECURE=true`. With one proxy in front of the bundled web server, set `YMER_TRUSTED_PROXIES=2`. [Bundle settings](packaging/bundle/README.md)

Docker named volumes store the database, uploaded files and Redis data. `ymerctl backup` writes the database and every stored file into one archive, and `ymerctl update` takes one before upgrading. Keep the matching private `ymer.env` too. For HTTPS on a public name, backups that leave the machine, monitoring and alerts, follow the [production guide](docs/production.md). Keep existing database and storage passwords when reusing volumes. **`docker compose down -v` deletes the project’s named volumes.** Use the stop commands above for normal shutdown.

- **Portable bundle:** Stop the old bundle, unpack the new release, copy the existing `ymer.env` into the new bundle, and run its `ymerctl update` (PowerShell equivalent on Windows). Use the same Docker engine and Compose project so the volumes are reused.
- **Windows installer:** Stop Ymer, run the new server installer in the same location, then choose **Start Ymer**.
- **Debian package:** Install the new server `.deb` with `sudo apt install ./filename.deb`, using its actual filename. The package keeps the configuration and restarts the service.

The **Update Ymer** shortcut and `ymerctl update` pull the images selected by the installed bundle. Install a newer package or bundle to change release versions.

## Private Vultr VM for two workspace owners

Use a fresh VM and the portable server bundle from a release containing `flask --app wsgi create-user`. Older images ignore the registration setting. Complete the registration check below before granting access to either client device.

1. Attach a Vultr firewall group. Keep Ymer’s web ports closed on public IPv4 and IPv6. During setup, restrict any temporary SSH rule to the administrator’s address. Verify private SSH and Vultr console recovery before removing that rule. [Vultr firewall groups](https://docs.vultr.com/products/network/firewall-groups)
2. Install Tailscale on the VM and both users’ devices. Give the VM a dedicated tag and grant exactly the two individual identities TCP 443 to that tag. Remove any overlapping grants or ACLs that already allow broader access. Give each identity the same application access. Decide separately who administers the VM. If both people maintain it, use two named Linux accounts, individual SSH keys, and identical private SSH grants. Docker and sudo access permit reading server secrets. [Tailscale grants](https://tailscale.com/docs/features/access-control/grants)
3. On the VM, unpack the portable bundle and run `./ymerctl init`. Configure private HTTPS with `sudo tailscale serve --bg http://127.0.0.1:8080` and record the HTTPS origin from `tailscale serve status`. Keep Funnel off for this service. [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)
4. Edit the existing `ymer.env` before the first start. Preserve the generated secrets. Set the values below, replacing the example hostname with the exact HTTPS origin from Serve. The public URL setting is the address clients use for signed downloads, even for a private server.

   ```dotenv
   YMER_HTTP_BIND=127.0.0.1
   YMER_HTTP_PORT=8080
   YMER_PUBLIC_URL=https://ymer.example-tailnet.ts.net
   YMER_COOKIE_SECURE=true
   YMER_TRUSTED_PROXIES=2
   YMER_REGISTRATION_ENABLED=false
   ```

5. From the bundle directory run the following commands. The registration endpoint must return `{"enabled":false}` and the POST must return **403**. A 404, 422 or any other result means the expected private configuration has not been verified. Each invocation of `create-user` prompts for a display name, email and hidden password. Run it once per person. Existing accounts keep their access when registration is closed.

   ```bash
   ./ymerctl start
   curl -fsS http://127.0.0.1:8080/api/auth/registration
   curl -sS -o /dev/null -w '%{http_code}\n' -H 'Content-Type: application/json' \
     -d '{}' http://127.0.0.1:8080/api/auth/register
   ./ymerctl compose exec api flask --app wsgi create-user
   ./ymerctl compose exec api flask --app wsgi create-user
   ```

6. Either person can create the shared workspace. Its creator opens **Members**, enters the other person’s account email, selects **owner**, and clicks **Add member**. Confirm that both rows show **owner**. Either owner can manage members, change the other owner’s role, or delete the workspace. At least one owner must remain.

**Acceptance checks.** Sign in separately over private HTTPS, open **Members** from both accounts, edit the same page together, and upload an attachment from one device and download it on the other. From a device outside the private network, verify that Ymer cannot be reached at the VM’s public addresses. Verify an unrelated tailnet identity cannot reach the private HTTPS service either.

**Recovery.** Schedule encrypted off-VM backups with `ymerctl backup` (set `YMER_BACKUP_PASSPHRASE` and `YMER_BACKUP_S3_*`; see [Backups](docs/production.md#3-backups)), and keep the matching private configuration and encryption secrets with them. On a separate private test VM restore the matching data and secrets, then verify that both owners can sign in and retrieve an attachment. Keep backup recovery available to both people if both will operate the service.

## Development from source

Use a Linux shell, either on Linux itself or inside a WSL 2 distribution. For WSL, enable your distribution under **Docker Desktop → Settings → Resources → WSL Integration** and clone into your Linux home directory. Install the development prerequisites **inside that distribution**.

You need Git, Bash, Python **3.12+** with `venv`, Node.js **22.18+**, npm, curl, and a running Docker engine with Compose. GNU Make is also needed if you select Docker for Factory builds. From your Linux shell:

```bash
git clone https://github.com/MKlolbullen/Ymer.git
cd Ymer
python3 --version
node --version
docker info
docker compose version
export COMPOSE_PROJECT_NAME=ymer-dev
./install.sh
```

Keep `COMPOSE_PROJECT_NAME=ymer-dev` set for **every development Compose command**, including in new terminals, when a release installation shares the same Docker engine. Both manifests otherwise default to the project name `ymer` and use the same volume names.

Open **http://localhost:5173** after the installer prints its ready message. The script prepares `.env`, installs the backend from `backend/pyproject.toml` and the two Node projects from their lockfiles, starts PostgreSQL, Redis and MinIO in Docker, and runs migrations. The API, collaboration server, frontend and configured workers run in the foreground on the host. Press **Ctrl+C** to stop them, then run `docker compose stop` in the same shell to stop the infrastructure.

- To install only the dependencies and prepare `.env`, run `./install.sh --install-only`.
- To select another Python interpreter, run, for example, `PYTHON=python3.12 ./install.sh`.
- Running `docker compose up` in the **repository root** starts development infrastructure only. Use `./install.sh` for the full development app or the release bundle for the fully containerized app.
- The default AI and Factory demo needs no API keys. To execute Factory builds in local Docker sandboxes, set `YMER_FACTORY_SANDBOX=docker` in the development `.env` and rerun the installer. The release bundle supports the demo or hosted Modal sandbox. [Development guide](DEVELOPMENT.md) · [Factory sandbox images](infra/factory/README.md)

## If startup fails

| Symptom | What to check |
| --- | --- |
| Docker CLI works but the server is unreachable | Run `docker info` in the same shell as Ymer. Start Docker Desktop or the Linux Docker service. For development inside WSL, check that distribution’s Docker integration. |
| Compose rejects `--wait` or the `env_file` syntax | Update Docker Desktop or the Linux Compose plugin. Run `docker compose version` again. |
| Release port 8080 is busy | Run `ymerctl init` if needed, change the port and public URL together, then restart. |
| Bundle stops during setup | Inspect `ymerctl status` and `ymerctl compose logs setup postgres minio`. If volumes already exist, verify that their original settings file is in use. |
| Development UI never becomes ready | Check the installer output. The API uses port 5000, collaboration defaults to 1234, and Vite needs 5173. Run `docker compose ps --all` in the checkout to inspect the backing services. |

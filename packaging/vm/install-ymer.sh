#!/usr/bin/env bash
# Install Ymer on a fresh Debian or Ubuntu VM (Vultr, Hetzner, DigitalOcean, ...), in one go.
#
#   curl -fsSLO https://raw.githubusercontent.com/MKlolbullen/Ymer/main/packaging/vm/install-ymer.sh
#   sudo bash install-ymer.sh --domain ymer.example.com --email you@example.com
#
# It installs Docker, a firewall, swap and automatic security updates, then the Ymer
# server package (systemd service, daily backups), configures HTTPS and starts Ymer.
# Run it again to upgrade or to change settings; your data and secrets are kept.
# `bash install-ymer.sh --help` lists the options. Guide: docs/production.md
set -euo pipefail

REPO=MKlolbullen/Ymer
# The copy attached to a release installs that release by default (set by the release build).
RELEASE_VERSION=""
ENV_FILE=/etc/ymer/ymer.env

usage() {
  cat <<'HELP'
Usage: sudo bash install-ymer.sh <how people reach it> [options]

How people reach Ymer (pick one):
  --domain NAME --email ADDRESS
                       Public HTTPS on NAME with a Let's Encrypt certificate. NAME's DNS
                       record must already point at this VM. Opens ports 80 and 443.
  --tailscale          Private HTTPS for your tailnet only (Tailscale Serve); no public ports.
                       Add --tailscale-authkey KEY to log in without a browser.
  --http               Plain HTTP on port 8080 to everyone. Only for a quick trial.

Options:
  --no-signups         Close public sign-ups. You are asked to create the first account
                       (more: sudo ymerctl compose exec api flask --app wsgi create-user).
  --anthropic-key-file FILE
                       Turn on Claude for the AI features with the key in FILE (the key is
                       never passed on the command line, where other users could see it).
  --alert-email ADDRESS  Where alerts go (also set YMER_SMTP_* with --set), or
  --alert-webhook URL  a Slack-compatible or ntfy webhook for alerts.
  --no-monitoring      Skip Prometheus, Alertmanager and Grafana.
  --set KEY=VALUE      Any setting for /etc/ymer/ymer.env (repeatable), e.g.
                       --set YMER_BACKUP_S3_URL=https://s3.eu-central-1.amazonaws.com
  --version VERSION    Install this release (default: the newest release with a server .deb).
  --build [REF]        Build Ymer from source at REF (default: main) instead of
                       downloading a release. Takes 10 to 20 minutes on a small VM.
  --deb FILE           Install this ymer_<version>_all.deb instead of downloading one.
  --no-firewall        Leave the firewall (ufw) alone.
  --yes                Do not ask before starting.
  -h, --help           Show this help.
HELP
}

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
fail() { printf '\033[31minstall-ymer: %s\033[0m\n' "$*" >&2; exit 1; }

mode="" domain="" email="" ts_key="" signups=true monitoring=true firewall=true yes=false
version="" build_ref="" local_deb="" anthropic_key_file="" alert_email="" alert_webhook=""
settings=()
while [ $# -gt 0 ]; do
  case $1 in
    --domain) domain=${2:?--domain needs a name}; mode=domain; shift ;;
    --email) email=${2:?--email needs an address}; shift ;;
    --tailscale) mode=tailscale ;;
    --tailscale-authkey) ts_key=${2:?}; shift ;;
    --http) mode=http ;;
    --no-signups) signups=false ;;
    --anthropic-key-file) anthropic_key_file=${2:?}; shift ;;
    --alert-email) alert_email=${2:?}; shift ;;
    --alert-webhook) alert_webhook=${2:?}; shift ;;
    --no-monitoring) monitoring=false ;;
    --set) [[ ${2:-} == *=* ]] || fail "--set needs KEY=VALUE"; settings+=("$2"); shift ;;
    --version) version=${2#v}; shift ;;
    --build) if [ -n "${2:-}" ] && [[ $2 != -* ]]; then build_ref=$2; shift; else build_ref=main; fi ;;
    --deb) local_deb=$(readlink -f -- "${2:?--deb needs a file}"); shift ;;
    --no-firewall) firewall=false ;;
    --yes|-y) yes=true ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; fail "Unknown option: $1" ;;
  esac
  shift
done

# --- Checks ---------------------------------------------------------------------------
[ "$(id -u)" = 0 ] || fail "Run it as root: sudo bash $0 ..."
[ -n "$mode" ] || { usage >&2; fail "Choose --domain, --tailscale or --http."; }
if [ "$mode" = domain ]; then
  [ -n "$email" ] || fail "--domain needs --email too (Let's Encrypt sends certificate notices there)."
  [[ $domain != *://* && $domain != */* ]] || fail "Give the name only, like ymer.example.com"
fi
[ -z "$anthropic_key_file" ] || [ -s "$anthropic_key_file" ] || fail "No key in $anthropic_key_file"
[ -r /etc/os-release ] || fail "Unknown Linux: /etc/os-release is missing."
. /etc/os-release
case " ${ID:-} ${ID_LIKE:-} " in
  *" debian "*|*" ubuntu "*) ;;
  *) fail "This installer supports Debian and Ubuntu (found ${PRETTY_NAME:-$ID}). Use the portable bundle elsewhere: see INSTALLATION.MD." ;;
esac
arch=$(dpkg --print-architecture)
case $arch in amd64|arm64) ;; *) fail "Ymer's images are built for amd64 and arm64, not $arch." ;; esac
has_systemd=false
[ -d /run/systemd/system ] && has_systemd=true

export DEBIAN_FRONTEND=noninteractive
mem_mb=$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo)
disk_gb=$(df -Pk / | awk 'NR == 2 {print int($4 / 1024 / 1024)}')

say "Installing Ymer on ${PRETTY_NAME:-Linux} ($arch, ${mem_mb} MB memory, ${disk_gb} GB free)"
case $mode in
  domain) note "Public HTTPS at https://$domain" ;;
  tailscale) note "Private HTTPS on your tailnet (Tailscale Serve)" ;;
  http) note "Plain HTTP on port 8080 (trial only: no encryption)" ;;
esac
[ "$mem_mb" -ge 1800 ] || note "Warning: under 2 GB of memory; Ymer needs about 2 GB (4 GB with monitoring)."
[ "$disk_gb" -ge 10 ] || fail "Only ${disk_gb} GB free on /; Ymer needs at least 10 GB."
if [ "$mode" = domain ]; then
  me=$(curl -fsS4 --max-time 5 https://api.ipify.org 2>/dev/null || true)
  dns=$(getent ahostsv4 "$domain" 2>/dev/null | awk 'NR == 1 {print $1}')
  if [ -z "$dns" ]; then
    fail "$domain does not resolve yet. Add an A record pointing to ${me:-the public IP of this VM}, wait a few minutes, then run this again."
  elif [ -n "$me" ] && [ "$dns" != "$me" ]; then
    note "Warning: $domain points to $dns but this VM's public IP is $me. The certificate cannot be issued until they match."
  fi
fi
if ! $yes && [ -t 0 ]; then
  read -r -p "    Continue? [Y/n] " answer
  case $answer in [nN]*) exit 1 ;; esac
fi

# --- Base system ----------------------------------------------------------------------
say "Updating packages and turning on automatic security updates"
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg openssl jq unattended-upgrades >/dev/null
echo 'APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";' >/etc/apt/apt.conf.d/20auto-upgrades

if [ "$(awk '/SwapTotal/ {print $2}' /proc/meminfo)" = 0 ] && [ "$mem_mb" -lt 4000 ] && [ ! -e /swapfile ]; then
  say "Adding 2 GB of swap (this VM has ${mem_mb} MB of memory)"
  if fallocate -l 2G /swapfile 2>/dev/null && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile 2>/dev/null; then
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
  else
    rm -f /swapfile
    note "Could not add swap here; carrying on without it."
  fi
fi

# --- Docker ---------------------------------------------------------------------------
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  say "Docker is already installed ($(docker --version | cut -d, -f1))"
else
  say "Installing Docker Engine and the Compose plugin from download.docker.com"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$arch signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
    >/etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
fi
if [ ! -f /etc/docker/daemon.json ]; then
  # Cap container logs so they cannot fill the disk.
  mkdir -p /etc/docker
  echo '{ "log-driver": "local", "log-opts": { "max-size": "50m", "max-file": "5" } }' >/etc/docker/daemon.json
  $has_systemd && systemctl restart docker
fi
if $has_systemd; then
  systemctl enable --now docker >/dev/null 2>&1
fi
docker info >/dev/null 2>&1 || fail "Docker is installed but not running. Check: systemctl status docker"

# --- Firewall -------------------------------------------------------------------------
if $firewall; then
  say "Configuring the firewall (ufw)"
  apt-get install -y -qq ufw >/dev/null
  ssh_port=$(sshd -T 2>/dev/null | awk '/^port / {print $2; exit}')
  ufw allow "${ssh_port:-22}/tcp" >/dev/null
  case $mode in
    domain) ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw allow 443/udp >/dev/null ;;
    http) ufw allow 8080/tcp >/dev/null ;;
  esac
  ufw --force enable >/dev/null
  note "Open: SSH (${ssh_port:-22})$([ "$mode" = domain ] && echo ', 80, 443')$([ "$mode" = http ] && echo ', 8080')."
  note "Docker publishes Grafana (3000) and plain HTTP (8080) on 127.0.0.1 only, so they stay private."
  [ "$mode" = http ] && note "Warning: Docker-published ports bypass ufw; 8080 is reachable from anywhere in --http mode."
fi

# --- Tailscale ------------------------------------------------------------------------
if [ "$mode" = tailscale ]; then
  if ! command -v tailscale >/dev/null; then
    say "Installing Tailscale"
    curl -fsSL https://tailscale.com/install.sh | sh >/dev/null
  fi
  if ! tailscale status >/dev/null 2>&1; then
    say "Connecting this VM to your tailnet"
    if [ -n "$ts_key" ]; then
      tailscale up --authkey "$ts_key"
    else
      note "Open the link below and log in, then this continues by itself."
      tailscale up
    fi
  fi
fi

# --- Ymer -----------------------------------------------------------------------------
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
deb=""
if [ -n "$local_deb" ]; then
  [ -f "$local_deb" ] || fail "No such file: $local_deb"
  deb=$local_deb
elif [ -n "$build_ref" ]; then
  say "Building Ymer from source ($REPO at $build_ref); this takes a while"
  apt-get install -y -qq git python3 zip >/dev/null
  git clone -q --depth 1 --branch "$build_ref" "https://github.com/$REPO.git" "$work/src" 2>/dev/null \
    || { git clone -q "https://github.com/$REPO.git" "$work/src" && git -C "$work/src" checkout -q "$build_ref"; }
  src_version=$(cat "$work/src/VERSION")
  image_tag="$src_version-src.$(git -C "$work/src" rev-parse --short HEAD)"
  for image in api collab web; do
    note "Building ymer-$image ..."
    docker build -q -t "ghcr.io/mklolbullen/ymer-$image:$image_tag" \
      -f "$work/src/packaging/docker/$image.Dockerfile" "$work/src" >/dev/null
  done
  (cd "$work/src" && packaging/build.sh deb >/dev/null)
  deb=$(ls "$work"/src/dist/ymer_*_all.deb)
  settings+=("YMER_VERSION=$image_tag")
else
  version=${version:-$RELEASE_VERSION}
  if [ -z "$version" ]; then
    version=$(curl -fsSL "https://api.github.com/repos/$REPO/releases?per_page=30" \
      | jq -r '[.[] | select(.draft | not) | select(any(.assets[]; .name | test("^ymer_.*_all\\.deb$")))][0].assets[] | select(.name | test("^ymer_.*_all\\.deb$")) | .name' \
      | sed -E 's/^ymer_(.*)_all\.deb$/\1/' | head -n 1)
    [ -n "$version" ] || fail "No published release has a server .deb yet. Run with --build to build from source."
  fi
  say "Downloading Ymer $version"
  url=$(curl -fsSL "https://api.github.com/repos/$REPO/releases?per_page=30" \
    | jq -r --arg n "ymer_${version}_all.deb" '[.[] | select(.draft | not) | .assets[] | select(.name == $n)][0].browser_download_url // empty')
  [ -n "$url" ] || fail "Release $version has no ymer_${version}_all.deb."
  curl -fsSL -o "$work/ymer.deb" "$url"
  deb="$work/ymer.deb"
fi

# Settings that must be in place before the package's first start.
first_install=false
if [ ! -f "$ENV_FILE" ]; then
  first_install=true
  install -d -m 755 /etc/ymer
  bind=127.0.0.1
  [ "$mode" = http ] && bind=0.0.0.0
  YMER_ENV_FILE=$ENV_FILE YMER_HTTP_BIND=$bind YMER_PUBLIC_URL="http://localhost:8080" \
    bash -c "$(dpkg-deb --fsys-tarfile "$deb" | tar -xO ./opt/ymer/ymerctl)" ymerctl init >/dev/null
fi

set_setting() {
  local key=$1 value=$2 tmp="$ENV_FILE.tmp"
  awk -v key="$key" -v value="$value" '
    BEGIN { done = 0 }
    $0 ~ "^#? ?" key "=" { if (!done) { print key "=" value; done = 1 }; next }
    { print }
    END { if (!done) print key "=" value }
  ' "$ENV_FILE" >"$tmp" && chmod 600 "$tmp" && mv "$tmp" "$ENV_FILE"
}

say "Configuring Ymer"
case $mode in
  domain)
    set_setting YMER_DOMAIN "$domain"
    set_setting YMER_ACME_EMAIL "$email"
    set_setting YMER_PUBLIC_URL "https://$domain"
    set_setting YMER_HTTP_BIND 127.0.0.1
    set_setting YMER_COOKIE_SECURE true
    set_setting YMER_TRUSTED_PROXIES 2
    set_setting YMER_LOG_FORMAT json
    ;;
  tailscale)
    ts_name=$(tailscale status --json | jq -r '.Self.DNSName' | sed 's/\.$//')
    [ -n "$ts_name" ] && [ "$ts_name" != null ] || fail "Could not read this VM's tailnet name."
    tailscale serve --bg http://127.0.0.1:8080 >/dev/null \
      || fail "tailscale serve failed. Turn on MagicDNS and HTTPS certificates in the Tailscale admin console (DNS page), then run this again."
    set_setting YMER_PUBLIC_URL "https://$ts_name"
    set_setting YMER_HTTP_BIND 127.0.0.1
    set_setting YMER_COOKIE_SECURE true
    set_setting YMER_TRUSTED_PROXIES 2
    set_setting YMER_LOG_FORMAT json
    set_setting YMER_DOMAIN ""
    ;;
  http)
    host=$(curl -fsS4 --max-time 5 https://api.ipify.org 2>/dev/null || hostname -f)
    set_setting YMER_PUBLIC_URL "http://$host:8080"
    set_setting YMER_HTTP_BIND 0.0.0.0
    set_setting YMER_COOKIE_SECURE false
    set_setting YMER_TRUSTED_PROXIES 1
    set_setting YMER_DOMAIN ""
    ;;
esac
set_setting YMER_MONITORING "$monitoring"
$signups || set_setting YMER_REGISTRATION_ENABLED false
$signups && $first_install && set_setting YMER_REGISTRATION_ENABLED true
if [ -n "$anthropic_key_file" ]; then
  set_setting YMER_AI_PROVIDER anthropic
  set_setting ANTHROPIC_API_KEY "$(tr -d '[:space:]' <"$anthropic_key_file")"
fi
[ -z "$alert_email" ] || set_setting YMER_ALERT_EMAIL_TO "$alert_email"
[ -z "$alert_webhook" ] || set_setting YMER_ALERT_WEBHOOK_URL "$alert_webhook"
for pair in "${settings[@]}"; do set_setting "${pair%%=*}" "${pair#*=}"; done
# Without the passphrase, backups are readable by anyone who gets a copy; generate one.
if [ -z "$(sed -n 's/^YMER_BACKUP_PASSPHRASE=//p' "$ENV_FILE")" ]; then
  set_setting YMER_BACKUP_PASSPHRASE "$(openssl rand -hex 24)"
  passphrase_new=true
fi

say "Installing the Ymer server package"
# The package (re)starts Ymer with the settings above; without systemd it starts below.
apt-get install -y -qq "$deb" >/dev/null
command -v ymerctl >/dev/null || fail "The package did not install ymerctl."
ymerctl help | grep -q '^  https' \
  || fail "Ymer $version predates the production tools (HTTPS, backups, monitoring). Use --build, or a newer release."

say "Starting Ymer (the first start downloads about 1.5 GB of images)"
if $has_systemd; then
  systemctl restart ymer
  systemctl enable --now ymer-backup.timer >/dev/null 2>&1 || true
else
  ymerctl restart
fi

if [ "$mode" = domain ]; then
  note "Waiting for the certificate for $domain ..."
  for _ in $(seq 1 30); do
    curl -fsS -o /dev/null --max-time 5 "https://$domain/api/health" 2>/dev/null && break
    sleep 4
  done
fi

say "First backup"
ymerctl backup >/dev/null 2>&1 && note "Written to /var/backups/ymer (daily from now on)." \
  || note "The first backup failed; run: sudo ymerctl backup"

if ! $signups && $first_install && [ -t 0 ]; then
  say "Create the first account (sign-ups are closed)"
  ymerctl compose exec api flask --app wsgi create-user || note "Later: sudo ymerctl compose exec api flask --app wsgi create-user"
fi

say "Readiness check"
ymerctl check || true

url=$(ymerctl url)
say "Ymer is installed: $url"
cat <<DONE
    Settings:  $ENV_FILE  (then: sudo systemctl restart ymer)
    Status:    sudo ymerctl status      Logs: sudo ymerctl logs api
    Backups:   sudo ymerctl backups     Restore: sudo ymerctl restore <file>
DONE
if $monitoring; then
  cat <<DONE
    Grafana:   ssh -L 3000:127.0.0.1:3000 ${SUDO_USER:-root}@<this VM>, then http://localhost:3000
               user admin, password: sudo grep GRAFANA_PASSWORD $ENV_FILE
DONE
  grep -qE '^YMER_ALERT_(EMAIL_TO|WEBHOOK_URL|SLACK_WEBHOOK)=.' "$ENV_FILE" \
    || note "Alerts have nowhere to go yet: rerun with --alert-email or --alert-webhook."
fi
if [ "${passphrase_new:-false}" = true ]; then
  note "Backups are encrypted with YMER_BACKUP_PASSPHRASE in $ENV_FILE."
  note "Store a copy of it in your password manager: without it the backups cannot be restored."
fi
grep -q '^YMER_BACKUP_S3_URL=.' "$ENV_FILE" \
  || note "Backups stay on this VM. For off-site copies set YMER_BACKUP_S3_* (docs/production.md#3-backups)."

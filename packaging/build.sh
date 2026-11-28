#!/usr/bin/env bash
# Build Ymer's server release files into dist/:
#
#   packaging/build.sh bundle   ymer-server-<version>.tar.gz and .zip (Compose bundle)
#   packaging/build.sh deb      ymer_<version>_all.deb (bundle + systemd service)
#
# The version comes from VERSION. Images are built separately from packaging/docker/.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
version=$(python3 scripts/version.py)
out=dist
mkdir -p "$out"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

stage_bundle() {
  local dir=$1
  mkdir -p "$dir"
  sed "s/:-@VERSION@}/:-$version}/" packaging/bundle/compose.yaml > "$dir/compose.yaml"
  install -m 755 packaging/bundle/ymerctl "$dir/ymerctl"
  install -m 644 packaging/bundle/ymerctl.ps1 packaging/bundle/README.md packaging/bundle/Caddyfile "$dir/"
  cp -R packaging/bundle/monitoring "$dir/monitoring"
  find "$dir/monitoring" -type d -exec chmod 755 {} + -o -type f -exec chmod 644 {} +
  echo "$version" > "$dir/VERSION"
}

bundle() {
  local name="ymer-server-$version"
  stage_bundle "$work/$name"
  tar -C "$work" --owner=0 --group=0 -czf "$out/$name.tar.gz" "$name"
  (cd "$work" && rm -f "$OLDPWD/$out/$name.zip" && zip -qr "$OLDPWD/$out/$name.zip" "$name")
  echo "$out/$name.tar.gz $out/$name.zip"
}

deb() {
  # Debian versions sort ~ before anything, so 0.1.0-beta.1 becomes 0.1.0~beta.1.
  local debversion=${version/-/\~}
  local root="$work/deb"
  stage_bundle "$root/opt/ymer"
  install -D -m 755 packaging/deb/ymerctl-wrapper "$root/usr/bin/ymerctl"
  install -D -m 644 packaging/deb/ymer.service "$root/lib/systemd/system/ymer.service"
  install -D -m 644 packaging/deb/ymer-backup.service "$root/lib/systemd/system/ymer-backup.service"
  install -D -m 644 packaging/deb/ymer-backup.timer "$root/lib/systemd/system/ymer-backup.timer"
  install -d -m 755 "$root/DEBIAN" "$root/etc/ymer"
  install -m 755 packaging/deb/postinst packaging/deb/prerm packaging/deb/postrm "$root/DEBIAN/"
  cat > "$root/DEBIAN/control" <<CONTROL
Package: ymer
Version: $debversion
Architecture: all
Maintainer: Ymer <noreply@github.com>
Homepage: https://github.com/MKlolbullen/Ymer
Section: web
Priority: optional
Depends: docker.io (>= 20.10) | docker-ce, docker-compose-v2 | docker-compose-plugin | docker-compose (>= 2), coreutils
Recommends: openssl, curl
Installed-Size: $(du -sk "$root" | cut -f1)
Description: Ymer server: collaborative documents, databases, mind maps and the Factory
 Runs the Ymer web app, API, realtime collaboration server, workers, PostgreSQL,
 Redis and MinIO as Docker containers under a systemd service (ymer.service).
 Manage it with ymerctl; settings are in /etc/ymer/ymer.env.
CONTROL
  # The file name keeps the plain version: GitHub release assets cannot contain "~".
  dpkg-deb --root-owner-group --build "$root" "$out/ymer_${version}_all.deb" >/dev/null
  echo "$out/ymer_${version}_all.deb"
}

case ${1:-} in
  bundle) bundle ;;
  deb) deb ;;
  *) echo "Usage: packaging/build.sh bundle|deb" >&2; exit 2 ;;
esac

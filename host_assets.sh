#!/usr/bin/env bash
# Host PNGs on the social-assets GitHub repo → Buffer-reachable raw URLs.
#
# WHY: the carousel/image pipeline needs public URLs for buffer_post.py /
# create_post. The old "cd /tmp/social-assets" flow was ephemeral (gone after
# reboot) and conflated with the render/code dir. This uses a DEDICATED clean
# clone (auto-created if absent), so hosting is decoupled and survives reboots.
#
# Usage:  host_assets.sh <file1.png> [file2.png ...]
#   Prints one raw URL per hosted file (in order) on stdout; diagnostics on stderr.
#   Override clone path with SOCIAL_ASSETS_CLONE=/path.
set -euo pipefail

CLONE="${SOCIAL_ASSETS_CLONE:-$HOME/social-assets-host}"
REPO="https://github.com/nellymarq/social-assets.git"
RAW="https://raw.githubusercontent.com/nellymarq/social-assets/main"

[ "$#" -ge 1 ] || { echo "usage: host_assets.sh <file.png> [...]" >&2; exit 2; }

if [ ! -d "$CLONE/.git" ]; then
  echo "cloning social-assets → $CLONE" >&2
  git clone --quiet --depth 1 "$REPO" "$CLONE"
fi
git -C "$CLONE" config user.email "nmarques1113@gmail.com"
git -C "$CLONE" config user.name "Nelly Marques"
# Clean clone — fast-forward to origin (depth 1 keeps it light).
git -C "$CLONE" fetch --quiet --depth 1 origin main
git -C "$CLONE" reset --hard --quiet origin/main

bases=()
for f in "$@"; do
  [ -f "$f" ] || { echo "missing file: $f" >&2; exit 1; }
  b="$(basename "$f")"
  cp -f "$f" "$CLONE/$b"
  git -C "$CLONE" add -- "$b"
  bases+=("$b")
done

if git -C "$CLONE" diff --cached --quiet; then
  echo "no changes to commit (files identical to remote)" >&2
else
  git -C "$CLONE" commit -q -m "Host assets: ${bases[*]}"
  git -C "$CLONE" push -q origin HEAD:main
fi

# Verify CDN propagation (raw cache ~5-60s) and emit URLs in order.
for b in "${bases[@]}"; do
  url="$RAW/$b"
  ok=""
  for i in $(seq 1 10); do
    code="$(curl -s -o /dev/null -w '%{http_code}' "$url" || true)"
    if [ "$code" = "200" ]; then ok=1; break; fi
    sleep 6
  done
  [ -n "$ok" ] || echo "WARN: $url not yet 200 (last=$code)" >&2
  echo "$url"
done

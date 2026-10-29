#!/bin/sh
# Has addons.mozilla.org sign the extension. The API key and secret are read from .dev/amo.env
# (git-ignored, owner-only) so they never appear on a command line or in shell history.
#
#   bun run sign           unlisted: Mozilla signs it for self-distribution and the signed .xpi
#                          lands in web-ext-artifacts/
#   bun run sign listed    the public listing on addons.mozilla.org (reviewed); the first one
#                          needs amo/listing.json (categories, summary, license)
#
# Every upload needs a new "version" in extension/manifest.json.
set -eu
cd "$(dirname "$0")/.."

KEYS=.dev/amo.env
CHANNEL="${1:-unlisted}"
VERSION=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' extension/manifest.json)

if [ ! -f "$KEYS" ]; then
  echo "[sign] no $KEYS; create one with WEB_EXT_API_KEY= and WEB_EXT_API_SECRET= lines from https://addons.mozilla.org/developers/addon/api/key/" >&2
  exit 1
fi
chmod 600 "$KEYS"
set -a
. "./$KEYS"
set +a
if [ -z "${WEB_EXT_API_KEY:-}" ] || [ -z "${WEB_EXT_API_SECRET:-}" ]; then
  echo "[sign] $KEYS needs both WEB_EXT_API_KEY and WEB_EXT_API_SECRET filled in" >&2
  exit 1
fi

set -- --source-dir extension --artifacts-dir web-ext-artifacts --channel "$CHANNEL"
if [ "$CHANNEL" = listed ]; then
  if [ ! -f amo/listing.json ]; then
    echo "[sign] the first listed version needs amo/listing.json (categories, summary, license)" >&2
    exit 1
  fi
  set -- "$@" --amo-metadata amo/listing.json
fi

started=$(date +%s)
echo "[sign] $(date +%T) submitting version $VERSION to the $CHANNEL channel" >&2
if bunx web-ext sign "$@"; then
  if [ "$CHANNEL" = unlisted ]; then
    # AMO names the download after a hash; keep a copy under the name the README uses.
    signed=$(ls -t web-ext-artifacts/*.xpi | head -n 1)
    cp "$signed" "web-ext-artifacts/couchcast-$VERSION.xpi"
    echo "[sign] $(date +%T) signed in $(($(date +%s) - started))s: web-ext-artifacts/couchcast-$VERSION.xpi (from $(basename "$signed"))" >&2
  else
    echo "[sign] $(date +%T) submitted in $(($(date +%s) - started))s; listed versions are published on addons.mozilla.org" >&2
  fi
else
  status=$?
  echo "[sign] $(date +%T) failed after $(($(date +%s) - started))s (exit $status)" >&2
  exit "$status"
fi

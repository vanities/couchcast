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
  echo "[sign] $(date +%T) done in $(($(date +%s) - started))s; signed files:" >&2
  ls -la web-ext-artifacts/*.xpi 2>/dev/null >&2 || echo "[sign] (no .xpi downloaded; listed versions stay on addons.mozilla.org)" >&2
else
  status=$?
  echo "[sign] $(date +%T) failed after $(($(date +%s) - started))s (exit $status)" >&2
  exit "$status"
fi

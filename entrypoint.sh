#!/bin/sh
set -e

PERSIST=/app/persistent
mkdir -p "$PERSIST/data" "$PERSIST/combo" "$PERSIST/Results" "$PERSIST/proxy"

[ ! -e /app/data ]    && ln -s "$PERSIST/data"    /app/data
[ ! -e /app/combo ]   && ln -s "$PERSIST/combo"   /app/combo
[ ! -e /app/Results ] && ln -s "$PERSIST/Results" /app/Results
[ ! -e /app/proxy ]   && ln -s "$PERSIST/proxy"   /app/proxy

echo "[entrypoint] persistent dir ready at $PERSIST"
exec python main.py

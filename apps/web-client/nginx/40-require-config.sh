#!/bin/sh
# The stock entrypoint only logs a template-rendering failure and carries on,
# leaving nginx running with no server block. Fail the container instead.
set -eu
conf="${NGINX_ENVSUBST_OUTPUT_DIR:-/etc/nginx/conf.d}/default.conf"
if [ ! -s "$conf" ]; then
  echo "40-require-config.sh: ERROR: $conf was not rendered (is the directory writable?)" >&2
  exit 1
fi
for f in /etc/nginx/tls/server.crt /etc/nginx/tls/server.key; do
  if [ ! -r "$f" ]; then
    echo "40-require-config.sh: ERROR: cannot read $f (run blockchain/scripts/gen-secrets.sh)" >&2
    exit 1
  fi
done

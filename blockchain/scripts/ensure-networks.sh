#!/usr/bin/env bash

set -euo pipefail

for net in kafka_net misinfo_net; do
  if ! docker network inspect "${net}" >/dev/null 2>&1; then
    echo ">> Creating docker network '${net}'..."
    docker network create "${net}" >/dev/null
  fi
done

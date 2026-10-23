#!/usr/bin/env bash
# Idempotently creates the Docker networks that are shared across
# independently-deployable services but not "owned" by any single one of
# their compose files — same reasoning as fabric_test (created by Fabric's
# own network.sh, not by any app's compose file): once a compose file
# declares a network external, an `include`-based aggregator can never
# un-external it, so nothing here creates kafka_net via `docker compose up`
# — it has to already exist first. Called from startup.sh; safe to re-run.
#
# misinfo_net joins the gateways, the app-layer services and the web edge so
# they reach one another by container name, with no host ports involved.
set -euo pipefail

for net in kafka_net misinfo_net; do
  if ! docker network inspect "${net}" >/dev/null 2>&1; then
    echo ">> Creating docker network '${net}'..."
    docker network create "${net}" >/dev/null
  fi
done

#!/usr/bin/env bash
set -euo pipefail

KAFKA_HOME=${KAFKA_HOME:-/opt/kafka}
CONFIG_FILE="${KAFKA_HOME}/config/kraft/server.properties"
LOG_DIRS=$(sed -n 's/^log.dirs=//p' "$CONFIG_FILE")
CLUSTER_ID_FILE="${LOG_DIRS}/cluster.id"

mkdir -p "$LOG_DIRS"

# Only format storage once per volume: reusing a cluster ID across restarts of
# the same data dir is required, re-formatting would wipe existing data.
if [ ! -f "${LOG_DIRS}/meta.properties" ]; then
    if [ -f "$CLUSTER_ID_FILE" ]; then
        CLUSTER_ID=$(cat "$CLUSTER_ID_FILE")
    else
        CLUSTER_ID=$("${KAFKA_HOME}/bin/kafka-storage.sh" random-uuid)
        echo "$CLUSTER_ID" > "$CLUSTER_ID_FILE"
    fi
    "${KAFKA_HOME}/bin/kafka-storage.sh" format -t "$CLUSTER_ID" -c "$CONFIG_FILE"
fi

# Declare the flagging-engine's message paths explicitly rather than relying on
# auto.create.topics.enable, which would create them with num.partitions=1
# (fine for one broker/one consumer, but partition count can only be raised
# later, never lowered, and raising it after messages exist breaks per-key
# ordering) - so pin partition counts here instead of leaving them accidental.
# Topic names must match KAFKA_INPUT_TOPIC / KAFKA_OUTPUT_TOPIC in the
# flagging-engine's app/config.py.
KAFKA_TOPICS=${KAFKA_TOPICS:-claims.raw,claims.flagged}
KAFKA_TOPIC_PARTITIONS=${KAFKA_TOPIC_PARTITIONS:-3}

(
    until "${KAFKA_HOME}/bin/kafka-broker-api-versions.sh" --bootstrap-server localhost:9092 >/dev/null 2>&1; do
        sleep 1
    done
    IFS=',' read -ra _topics <<< "$KAFKA_TOPICS"
    for topic in "${_topics[@]}"; do
        "${KAFKA_HOME}/bin/kafka-topics.sh" --bootstrap-server localhost:9092 \
            --create --if-not-exists --topic "$topic" \
            --partitions "$KAFKA_TOPIC_PARTITIONS" --replication-factor 1
    done
) &

exec "${KAFKA_HOME}/bin/kafka-server-start.sh" "$CONFIG_FILE"

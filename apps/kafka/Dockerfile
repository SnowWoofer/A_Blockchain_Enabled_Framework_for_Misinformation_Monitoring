FROM eclipse-temurin:17-jre-jammy

ARG KAFKA_VERSION=4.1.2
ARG SCALA_VERSION=2.13

ENV KAFKA_HOME=/opt/kafka
ENV PATH=${KAFKA_HOME}/bin:${PATH}

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && curl -fsSL "https://downloads.apache.org/kafka/${KAFKA_VERSION}/kafka_${SCALA_VERSION}-${KAFKA_VERSION}.tgz" -o /tmp/kafka.tgz \
    && mkdir -p "${KAFKA_HOME}" \
    && tar -xzf /tmp/kafka.tgz -C "${KAFKA_HOME}" --strip-components=1 \
    && rm /tmp/kafka.tgz \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY server.properties ${KAFKA_HOME}/config/kraft/server.properties
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

VOLUME ["/var/lib/kafka/data"]
EXPOSE 9092 9093 9094

ENTRYPOINT ["entrypoint.sh"]

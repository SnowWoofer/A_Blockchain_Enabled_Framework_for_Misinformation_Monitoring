FROM node:20-bookworm-slim

WORKDIR /caliper

# Install Caliper CLI and bind it to Fabric via the official @hyperledger/fabric-gateway SDK
RUN npm install --only=prod @hyperledger/caliper-cli@0.7.1 \
    && npx caliper bind --caliper-bind-sut fabric:fabric-gateway

ENV CALIPER_WORKSPACE=/caliper/workspace \
    CALIPER_BENCHCONFIG=benchmarks/misinformation-benchmark.yaml \
    CALIPER_NETWORKCONFIG=networks/fabric/test-network.yaml \
    CALIPER_FABRIC_GATEWAY_ENABLED=true

ENTRYPOINT ["npx", "caliper", "launch", "manager"]

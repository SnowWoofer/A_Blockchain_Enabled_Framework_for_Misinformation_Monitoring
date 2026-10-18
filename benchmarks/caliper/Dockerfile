FROM node:22-bookworm-slim
WORKDIR /caliper
RUN npm install -g npm@11.5.1
RUN npm install --omit=dev @hyperledger/caliper-cli@0.7.1 \
    && npx caliper bind --caliper-bind-sut fabric:fabric-gateway
ENTRYPOINT ["npx", "caliper"]

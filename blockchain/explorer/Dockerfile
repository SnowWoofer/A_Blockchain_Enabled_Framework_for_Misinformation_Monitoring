FROM ghcr.io/hyperledger-labs/explorer:latest

COPY patch_lscc.js /tmp/patch_lscc.js
RUN node /tmp/patch_lscc.js && rm /tmp/patch_lscc.js

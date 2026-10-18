FROM ghcr.io/hyperledger-labs/explorer:2.0.0@sha256:a52b1439dee979f2f5b64eb45c68ba4615ea3293d7c7778eff5b300197a925c4

COPY patch_lscc.js /tmp/patch_lscc.js
RUN node /tmp/patch_lscc.js && rm /tmp/patch_lscc.js

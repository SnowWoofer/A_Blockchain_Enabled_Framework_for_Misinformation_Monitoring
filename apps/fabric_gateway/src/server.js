'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('node:crypto');
const express = require('express');
const grpc = require('@grpc/grpc-js');
const { connect, signers } = require('@hyperledger/fabric-gateway');

const CRYPTO_PATH = process.env.CRYPTO_PATH || '/crypto';
const CHANNEL = process.env.CHANNEL || 'mychannel';
const CHAINCODE = process.env.CHAINCODE || 'misinformation';
const PORT = parseInt(process.env.PORT || '9100', 10);
const ORGS = (process.env.ORGS || 'org1,org2,org3')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);
const GATEWAY_PEER = process.env.GATEWAY_PEER || 'peer0.org1.example.com';
const GATEWAY_TARGET = process.env.GATEWAY_TARGET || `${GATEWAY_PEER}:7051`;
const TLS_NAME_OVERRIDE = process.env.TLS_NAME_OVERRIDE === '1';
const INVOKE_TIMEOUT_MS = parseInt(process.env.INVOKE_TIMEOUT_MS || '60000', 10);
const QUERY_TIMEOUT_MS = parseInt(process.env.QUERY_TIMEOUT_MS || '15000', 10);

const PEER_ORG_PREFIX = path.join(CRYPTO_PATH, 'peerOrganizations');
const ORG_DOMAIN = GATEWAY_PEER.replace(/^[^.]+\./, ''); // org1.example.com

function readPrivateKey(dir) {
  const file = fs.readdirSync(dir).find((f) => !f.startsWith('.'));
  if (!file) throw new Error(`no private key found in ${dir}`);
  return fs.readFileSync(path.join(dir, file));
}

// Which identity an org signs with. Defaults to Admin for backwards
// compatibility, but submitting claims and fact-checks only needs a *client*
// identity: the channel's Writers policy is OR('OrgNMSP.admin',
// 'OrgNMSP.client'), while OR('OrgNMSP.admin') alone gates channel-config
// changes. A member that only ever fact-checks should therefore sign as
// User1 (OU=client) and keep its admin key offline — set IDENTITY_ORG4=User1.
function identityNameFor(num) {
  return process.env[`IDENTITY_ORG${num}`] || 'Admin';
}

function loadIdentity(org) {
  const num = org.replace(/^org/, '');
  const user = `${identityNameFor(num)}@org${num}.example.com`;
  const mspDir = path.join(
    PEER_ORG_PREFIX,
    `org${num}.example.com`,
    'users',
    user,
    'msp'
  );
  return {
    mspId: `Org${num}MSP`,
    cert: fs.readFileSync(path.join(mspDir, 'signcerts', `${user}-cert.pem`)),
    key: readPrivateKey(path.join(mspDir, 'keystore')),
  };
}

function newGrpcConnection() {
  const tlsRootCert = fs.readFileSync(
    path.join(PEER_ORG_PREFIX, ORG_DOMAIN, 'peers', GATEWAY_PEER, 'tls', 'ca.crt')
  );
  const creds = grpc.credentials.createSsl(tlsRootCert);
  const options = {
    'grpc.keepalive_time_ms': 120000,
    'grpc.keepalive_timeout_ms': 20000,
    'grpc.keepalive_permit_without_calls': 1,
  };
  if (TLS_NAME_OVERRIDE) options['grpc.ssl_target_name_override'] = GATEWAY_PEER;
  return new grpc.Client(GATEWAY_TARGET, creds, options);
}

const grpcClient = newGrpcConnection();
const contracts = new Map(); // org -> Contract

function getContract(org) {
  if (!ORGS.includes(org)) {
    throw new Error(`org "${org}" not configured (configured: ${ORGS.join(',')})`);
  }
  if (!contracts.has(org)) {
    const id = loadIdentity(org);
    const gateway = connect({
      client: grpcClient,
      identity: { mspId: id.mspId, credentials: id.cert },
      signer: signers.newPrivateKeySigner(crypto.createPrivateKey(id.key)),
      evaluateOptions: () => ({ deadline: Date.now() + QUERY_TIMEOUT_MS }),
      endorseOptions: () => ({ deadline: Date.now() + INVOKE_TIMEOUT_MS }),
      submitOptions: () => ({ deadline: Date.now() + INVOKE_TIMEOUT_MS }),
      commitStatusOptions: () => ({ deadline: Date.now() + INVOKE_TIMEOUT_MS }),
    });
    contracts.set(org, gateway.getNetwork(CHANNEL).getContract(CHAINCODE));
  }
  return contracts.get(org);
}

// ------------------------------------------------------------------ app ----
const app = express();
app.use(express.json({ limit: '1mb' }));

app.get('/health', (_req, res) => {
  res.json({
    status: 'ok',
    sdk: '@hyperledger/fabric-gateway',
    channel: CHANNEL,
    chaincode: CHAINCODE,
    gateway_peer: GATEWAY_PEER,
    gateway_target: GATEWAY_TARGET,
    identities: ORGS,
  });
});

// GatewayError (EndorseError/SubmitError/CommitError) carries the real
// chaincode-thrown message per endorsing peer in `.details[].message`; the
// top-level `.message` is just a generic gRPC status ("10 ABORTED: failed to
// endorse transaction..."). Prefer the detail messages when present.
function errorMessage(err) {
  if (Array.isArray(err.details) && err.details.length) {
    const messages = [...new Set(err.details.map((d) => d.message).filter(Boolean))];
    if (messages.length) return messages.join('; ');
  }
  return String(err.message || err);
}

function parseResult(buf) {
  const text = buf.length ? buf.toString('utf8') : '';
  try {
    return JSON.parse(text);
  } catch (_) {
    return text;
  }
}

app.post('/invoke', async (req, res) => {
  const { org = 'org1', function: fn, args = [] } = req.body || {};
  if (!fn) return res.status(400).json({ error: 'missing "function"' });
  try {
    const contract = getContract(org);
    const submitted = await contract.submitAsync(fn, { arguments: args.map(String) });
    const status = await submitted.getStatus(); // waits for commit on ordering
    if (!status.successful) {
      return res.status(500).json({
        ok: false, function: fn, org,
        status: `COMMIT_FAILED(code=${status.code})`,
        tx_id: status.transactionId,
      });
    }
    res.json({
      ok: true, status: 'SUCCESS', tx_id: status.transactionId,
      function: fn, org, result: parseResult(submitted.getResult()),
    });
  } catch (err) {
    res.status(500).json({ ok: false, function: fn, org, error: errorMessage(err) });
  }
});

app.post('/query', async (req, res) => {
  const { org = 'org1', function: fn, args = [] } = req.body || {};
  if (!fn) return res.status(400).json({ error: 'missing "function"' });
  try {
    const result = await getContract(org).evaluateTransaction(fn, ...args.map(String));
    res.json({ ok: true, function: fn, org, result: parseResult(result) });
  } catch (err) {
    res.status(500).json({ ok: false, function: fn, org, error: errorMessage(err) });
  }
});

app.listen(PORT, () => {
  console.log(
    `[gateway-service] listening on :${PORT} | channel=${CHANNEL} cc=${CHAINCODE} ` +
      `peer=${GATEWAY_TARGET} override=${TLS_NAME_OVERRIDE} orgs=[${ORGS.join(',')}]`
  );
});

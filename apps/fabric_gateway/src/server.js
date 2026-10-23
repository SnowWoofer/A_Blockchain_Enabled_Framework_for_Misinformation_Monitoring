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
// Shared secret the blockchain gateway presents as X-Internal-Token. This
// service holds every org's signing keys, so nothing reaches /invoke or /query
// without it.
const INTERNAL_API_TOKEN = process.env.INTERNAL_API_TOKEN || '';
const DEFAULT_ROLE = (process.env.GATEWAY_ROLE || 'official').trim();
const ROLES = ['official', 'fact_checker'];

// Only the misinformation contract's own transactions are callable, and
// read-only functions only through /query.
const INVOKE_FUNCTIONS = new Set([
  'RegisterOrg', 'RequestOrgAdmission', 'VoteOnOrgAdmission', 'FinalizeOrgAdmission',
  'SetFoundingOrgLimit', 'Submit', 'SubmitFactCheck', 'FinalizeReport', 'ExpireReport',
]);
const QUERY_FUNCTIONS = new Set([
  'ListRegisteredOrgs', 'QueryOrgAdmission', 'QueryReport', 'QueryAllReports', 'QueryReportHistory',
]);
const MAX_ARGS = 16;
const MAX_ARG_LENGTH = 4096;

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
//
// For ABAC, role-bearing identities are registered by register-roles.sh:
//   officialN@orgN.example.com     — role=official     (admin/governance)
//   fact_checkerN@orgN.example.com — role=fact_checker (submit/fact-check)
// Each request names the role it acts under (the gateway passes the caller's
// role), so the chaincode sees a fact-checker as a fact-checker rather than
// every call signing as official. GATEWAY_ROLE is the default for requests
// that name none.
function identityNamesFor(num, role) {
  if (role === 'official') return [`official${num}`];
  if (role === 'fact_checker') return [`fact_checker${num}`, `factchecker${num}`];
  return [process.env[`IDENTITY_ORG${num}`] || 'Admin'];
}

function loadCert(signcertsDir, user) {
  // cryptogen produces <user>-cert.pem; Fabric CA produces cert.pem
  const preferred = path.join(signcertsDir, `${user}-cert.pem`);
  if (fs.existsSync(preferred)) return fs.readFileSync(preferred);
  const fallback = path.join(signcertsDir, 'cert.pem');
  if (fs.existsSync(fallback)) return fs.readFileSync(fallback);
  throw new Error(`no certificate found in ${signcertsDir}`);
}

function loadIdentity(org, role) {
  const num = org.replace(/^org/, '');
  const usersDir = path.join(PEER_ORG_PREFIX, `org${num}.example.com`, 'users');
  const candidates = identityNamesFor(num, role).map((name) => `${name}@org${num}.example.com`);
  const user = candidates.find((u) => fs.existsSync(path.join(usersDir, u, 'msp'))) || candidates[0];
  const mspDir = path.join(usersDir, user, 'msp');
  return {
    mspId: `Org${num}MSP`,
    cert: loadCert(path.join(mspDir, 'signcerts'), user),
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

let grpcClient = null; // connected on first use, so the module loads without crypto (tests)
const contracts = new Map(); // `${org}:${role}` -> Contract

function getContract(org, role) {
  if (!ORGS.includes(org)) {
    throw new Error(`org "${org}" not configured (configured: ${ORGS.join(',')})`);
  }
  const key = `${org}:${role}`;
  if (!contracts.has(key)) {
    const id = loadIdentity(org, role);
    if (!grpcClient) grpcClient = newGrpcConnection();
    const gateway = connect({
      client: grpcClient,
      identity: { mspId: id.mspId, credentials: id.cert },
      signer: signers.newPrivateKeySigner(crypto.createPrivateKey(id.key)),
      evaluateOptions: () => ({ deadline: Date.now() + QUERY_TIMEOUT_MS }),
      endorseOptions: () => ({ deadline: Date.now() + INVOKE_TIMEOUT_MS }),
      submitOptions: () => ({ deadline: Date.now() + INVOKE_TIMEOUT_MS }),
      commitStatusOptions: () => ({ deadline: Date.now() + INVOKE_TIMEOUT_MS }),
    });
    contracts.set(key, gateway.getNetwork(CHANNEL).getContract(CHAINCODE));
  }
  return contracts.get(key);
}

// ------------------------------------------------------------------ app ----
const app = express();
app.disable('x-powered-by');
app.use(express.json({ limit: '64kb' }));

function tokenMatches(presented) {
  if (!INTERNAL_API_TOKEN || typeof presented !== 'string') return false;
  const a = crypto.createHash('sha256').update(presented).digest();
  const b = crypto.createHash('sha256').update(INTERNAL_API_TOKEN).digest();
  return crypto.timingSafeEqual(a, b);
}

function requireInternalToken(req, res, next) {
  if (!tokenMatches(req.get('X-Internal-Token'))) {
    return res.status(401).json({ ok: false, error: 'missing or invalid X-Internal-Token' });
  }
  next();
}

// Validates and normalises an /invoke or /query body; returns an error string
// or the parsed call.
function parseCall(body, allowed) {
  const { org = 'org1', role = DEFAULT_ROLE, function: fn, args = [] } = body || {};
  if (!fn) return { error: 'missing "function"' };
  if (!allowed.has(fn)) return { error: `function "${fn}" is not allowed here` };
  if (!ROLES.includes(role)) return { error: `role must be one of ${ROLES.join(', ')}` };
  if (typeof org !== 'string' || !/^org[0-9]{1,3}$/.test(org)) return { error: 'malformed org' };
  if (!Array.isArray(args) || args.length > MAX_ARGS) return { error: `args must be an array of at most ${MAX_ARGS}` };
  const strArgs = args.map(String);
  if (strArgs.some((a) => a.length > MAX_ARG_LENGTH)) return { error: 'argument too long' };
  return { org, role, fn, args: strArgs };
}

app.get('/health', (_req, res) => {
  res.json({
    status: 'ok',
    sdk: '@hyperledger/fabric-gateway',
    channel: CHANNEL,
    chaincode: CHAINCODE,
    gateway_peer: GATEWAY_PEER,
    gateway_target: GATEWAY_TARGET,
    default_role: DEFAULT_ROLE,
    roles: ROLES,
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

app.post('/invoke', requireInternalToken, async (req, res) => {
  const call = parseCall(req.body, INVOKE_FUNCTIONS);
  if (call.error) return res.status(400).json({ ok: false, error: call.error });
  const { org, role, fn, args } = call;
  try {
    const contract = getContract(org, role);
    const submitted = await contract.submitAsync(fn, { arguments: args });
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

app.post('/query', requireInternalToken, async (req, res) => {
  const call = parseCall(req.body, QUERY_FUNCTIONS);
  if (call.error) return res.status(400).json({ ok: false, error: call.error });
  const { org, role, fn, args } = call;
  try {
    const result = await getContract(org, role).evaluateTransaction(fn, ...args);
    res.json({ ok: true, function: fn, org, result: parseResult(result) });
  } catch (err) {
    res.status(500).json({ ok: false, function: fn, org, error: errorMessage(err) });
  }
});

module.exports = { app, parseCall, tokenMatches };

if (require.main === module) {
  if (!INTERNAL_API_TOKEN) {
    console.error('[gateway-service] INTERNAL_API_TOKEN is not set; refusing to expose signing keys ' +
      'unauthenticated (run blockchain/scripts/gen-secrets.sh)');
    process.exit(1);
  }
  app.listen(PORT, () => {
    console.log(
      `[gateway-service] listening on :${PORT} | channel=${CHANNEL} cc=${CHAINCODE} ` +
        `peer=${GATEWAY_TARGET} override=${TLS_NAME_OVERRIDE} default_role=${DEFAULT_ROLE} orgs=[${ORGS.join(',')}]`
    );
  });
}

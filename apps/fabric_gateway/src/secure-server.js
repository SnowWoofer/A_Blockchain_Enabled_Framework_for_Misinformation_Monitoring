'use strict';

// One process owns exactly one organisation's client identities. Caller input
// can neither select another MSP nor select a more privileged certificate.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const express = require('express');
const grpc = require('@grpc/grpc-js');
const { connect, signers } = require('@hyperledger/fabric-gateway');

const ORG = process.env.ORG;
if (!/^org[1-9][0-9]*$/.test(ORG || '')) throw new Error('ORG is required');
const NUM = ORG.slice(3);
const ROOT = process.env.CRYPTO_PATH || '/crypto';
const registryPath = process.env.SIGNER_CREDENTIALS_FILE;
JSON.parse(fs.readFileSync(registryPath, 'utf8')); // fail closed at startup
const roleUsers = { official: `official${NUM}`, fact_checker: `factchecker${NUM}`, observer: `observer${NUM}` };
const reads = new Set(['QueryReport', 'QueryAllReports', 'QueryReportsPage', 'QueryReportHistory', 'QueryInterventions', 'ListRegisteredOrgs', 'QueryOrgAdmission']);
const writes = {
  official: new Set(['RegisterOrg', 'SetFoundingOrgLimit', 'RequestOrgAdmission', 'VoteOnOrgAdmission', 'FinalizeOrgAdmission', 'Submit', 'SubmitReport', 'SubmitFactCheck', 'FinalizeReport', 'ExpireReport', 'RecordIntervention']),
  fact_checker: new Set(['Submit', 'SubmitReport', 'SubmitFactCheck', 'RecordIntervention']),
  observer: new Set(),
};

function roleFor(key) {
  const digest = crypto.createHash('sha256').update(key || '').digest();
  let registry;
  try { registry = JSON.parse(fs.readFileSync(registryPath, 'utf8')); } catch (_) { return null; }
  const row = registry.find((r) => !r.revoked && /^[0-9a-f]{64}$/.test(r.digest) && crypto.timingSafeEqual(digest, Buffer.from(r.digest, 'hex')));
  return row && writes[row.role] ? row.role : null;
}

function onlyFile(dir) {
  const names = fs.readdirSync(dir).filter((n) => !n.startsWith('.'));
  if (names.length !== 1) throw new Error('identity directory must contain one file');
  return fs.readFileSync(path.join(dir, names[0]));
}

const tls = grpc.credentials.createSsl(fs.readFileSync(process.env.PEER_TLS_CA || '/peer-tls-ca.crt'));
const grpcClient = new grpc.Client(process.env.GATEWAY_TARGET || 'peer1:7051', tls,
  { 'grpc.ssl_target_name_override': process.env.GATEWAY_PEER || 'peer0.org1.example.com' });
const gateways = new Map();

function contract(role) {
  if (!gateways.has(role)) {
    const msp = path.join(ROOT, 'users', `${roleUsers[role]}@org${NUM}.example.com`, 'msp');
    const gateway = connect({ client: grpcClient,
      identity: { mspId: `Org${NUM}MSP`, credentials: onlyFile(path.join(msp, 'signcerts')) },
      signer: signers.newPrivateKeySigner(crypto.createPrivateKey(onlyFile(path.join(msp, 'keystore')))),
      evaluateOptions: () => ({ deadline: Date.now() + 60000 }),
      endorseOptions: () => ({ deadline: Date.now() + 60000 }),
      submitOptions: () => ({ deadline: Date.now() + 60000 }),
      commitStatusOptions: () => ({ deadline: Date.now() + 60000 }),
    });
    gateways.set(role, gateway);
  }
  return gateways.get(role).getNetwork(process.env.CHANNEL || 'mychannel').getContract(process.env.CHAINCODE || 'misinformation');
}

function decode(value) {
  const text = Buffer.from(value).toString('utf8');
  if (!text) return null;
  try { return JSON.parse(text); } catch (_) { return text; }
}

const app = express();
app.use(express.json({ limit: '1mb' }));
app.get('/health', (_req, res) => res.json({ status: 'ok', organisation: ORG, schema_version: 3 }));
for (const route of ['query', 'invoke']) {
  app.post(`/${route}`, async (req, res) => {
    const role = roleFor(req.get('X-API-Key'));
    if (!role) return res.status(401).json({ error: 'invalid credential' });
    const { function: fn, args = [], org } = req.body || {};
    if (org !== undefined || !Array.isArray(args)) return res.status(400).json({ error: 'organisation selection is forbidden; args must be an array' });
    if (!(route === 'query' ? reads.has(fn) : writes[role].has(fn))) return res.status(403).json({ error: 'operation is not permitted' });
    try {
      let result, txId;
      if (route === 'query') result = await contract(role).evaluateTransaction(fn, ...args.map(String));
      else {
        const tx = await contract(role).submitAsync(fn, { arguments: args.map(String) });
        const status = await tx.getStatus();
        if (!status.successful) return res.status(409).json({ ok: false, error: `commit rejected (${status.code})`, tx_id: status.transactionId });
        result = tx.getResult(); txId = status.transactionId;
      }
      return res.json({ ok: true, result: decode(result), tx_id: txId });
    } catch (err) {
      const details = Array.isArray(err.details) ? err.details.map((d) => d.message).filter(Boolean).join('; ') : '';
      const code = Number(err.code);
      return res.status(code === 14 ? 503 : code === 4 ? 504 : 400).json({ ok: false, error: details || String(err.message || err) });
    }
  });
}

app.listen(Number(process.env.PORT || 9100), '0.0.0.0');
process.on('SIGTERM', () => { for (const g of gateways.values()) g.close(); grpcClient.close(); process.exit(0); });

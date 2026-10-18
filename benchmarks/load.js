'use strict';
// Direct Fabric SDK measurements, run inside one isolated signer container.
// This deliberately excludes REST/IPFS/AI costs; workflow timing is separate.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { performance } = require('node:perf_hooks');
const grpc = require('/app/node_modules/@grpc/grpc-js');
const { connect, signers } = require('/app/node_modules/@hyperledger/fabric-gateway');

const cfg = JSON.parse(process.argv[2]);
const num = process.env.ORG.slice(3);
const msp = `/crypto/users/official${num}@org${num}.example.com/msp`;
const one = (dir) => fs.readFileSync(path.join(dir, fs.readdirSync(dir).find((f) => !f.startsWith('.'))));
const client = new grpc.Client(process.env.GATEWAY_TARGET, grpc.credentials.createSsl(fs.readFileSync('/peer-tls-ca.crt')),
  { 'grpc.ssl_target_name_override': process.env.GATEWAY_PEER });
const gateway = connect({ client,
  identity: { mspId: `Org${num}MSP`, credentials: one(`${msp}/signcerts`) },
  signer: signers.newPrivateKeySigner(crypto.createPrivateKey(one(`${msp}/keystore`))),
  evaluateOptions: () => ({ deadline: Date.now() + 60000 }),
  endorseOptions: () => ({ deadline: Date.now() + 60000 }),
  submitOptions: () => ({ deadline: Date.now() + 60000 }),
  commitStatusOptions: () => ({ deadline: Date.now() + 60000 }),
});
const contract = gateway.getNetwork('mychannel').getContract('misinformation');
const parse = (v) => JSON.parse(Buffer.from(v).toString('utf8') || 'null');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, Math.max(0, ms)));
const prefix = cfg.prefix || `run-${Date.now()}`;
const samples = [];
const generatorSourceHash = crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex');

async function transact(i) {
  const started = performance.now();
  const row = { index: i, scheduled_ms: i * 1000 / cfg.rate, started_ms: started - globalStart };
  try {
    if (cfg.operation === 'write' || cfg.operation === 'seed') {
      const id = `${prefix}-${String(i).padStart(8, '0')}`;
      const digest = crypto.createHash('sha256').update(id).digest('hex');
      const tx = await contract.submitAsync('Submit', { arguments: [id, digest, '1', '.9', 'benchmark-v3', new Date().toISOString()] });
      row.tx_id = tx.getTransactionId();
      const status = await tx.getStatus();
      if (!status.successful) throw new Error(`commit-${status.code}`);
      row.tx_id = status.transactionId;
    } else if (cfg.operation === 'point') {
      await contract.evaluateTransaction('QueryReport', cfg.pointId);
    } else if (cfg.operation === 'page') {
      await contract.evaluateTransaction('QueryReportsPage', '', '50', '');
    } else if (cfg.operation === 'scan') {
      await contract.evaluateTransaction('QueryAllReports');
    } else throw new Error('unknown operation');
    row.success = true;
  } catch (err) {
    row.success = false;
    row.error = String(err.message || err).slice(0, 300);
  }
  row.latency_ms = performance.now() - started;
  samples.push(row);
}

let globalStart;
(async () => {
  const initial = parse(await contract.evaluateTransaction('QueryAllReports')) || [];
  if (cfg.operation === 'count') { console.log(JSON.stringify({ records: initial.length })); return; }
  if (cfg.operation === 'point' && !cfg.pointId) cfg.pointId = initial[0]?.id;
  const count = cfg.count || Math.round(cfg.rate * cfg.duration);
  globalStart = performance.now();
  const inFlight = new Set();
  let maxConcurrency = 0, generatorBackpressure = false;
  for (let i = 0; i < count; i++) {
    await sleep(globalStart + i * 1000 / cfg.rate - performance.now());
    if (inFlight.size >= (cfg.maxInflight || 500)) {
      generatorBackpressure = true;
      // Keep the offered schedule and bounded memory. Count generator refusal
      // separately from a ledger failure; never turn 60 seconds into minutes
      // of catch-up traffic and still call that a 50-TPS offered round.
      samples.push({ index:i,scheduled_ms:i*1000/cfg.rate,started_ms:performance.now()-globalStart,
        success:false,error:'generator_inflight_limit',latency_ms:0 });
      continue;
    }
    const task = transact(i);
    inFlight.add(task); task.finally(() => inFlight.delete(task));
    maxConcurrency = Math.max(maxConcurrency, inFlight.size);
  }
  await Promise.all(inFlight);
  const elapsed = (performance.now() - globalStart) / 1000;
  const good = samples.filter((r) => r.success).map((r) => r.latency_ms).sort((a, b) => a - b);
  const percentile = (p) => good.length ? good[Math.ceil(p * good.length) - 1] : null;
  let reconciliation = null;
  if (cfg.operation === 'write' || cfg.operation === 'seed') {
    await sleep(2500);
    try {
      const records = parse(await contract.evaluateTransaction('QueryAllReports')) || [];
      const observed = records.filter((r)=>r.id.startsWith(prefix+'-')).length;
      reconciliation = { reports_observed:observed,unacknowledged_committed_reports:observed-good.length,
        observed_at_s_after_start:(performance.now()-globalStart)/1000,
        note:'Post-drain observation, outside latency/throughput timer; failed acknowledgements can still have committed.' };
    } catch (_) { reconciliation = { unavailable:true }; }
  }
  console.log(JSON.stringify({ configuration: cfg, generator_source_sha256:generatorSourceHash,
    initial_ledger_records: initial.length, commit_reconciliation:reconciliation,samples,
    summary: { attempted: samples.length, succeeded: good.length, failed: samples.length - good.length,
      generator_refused: samples.filter((r)=>r.error==='generator_inflight_limit').length,
      generator_policy:'fixed offered schedule; refuse above maxInflight and report separately',
      elapsed_s: elapsed, successful_throughput_tps: good.length / elapsed,
      mean_latency_ms: good.length ? good.reduce((a, b) => a + b, 0) / good.length : null,
      p50_latency_ms: percentile(.5), p95_latency_ms: percentile(.95), p99_latency_ms: percentile(.99),
      max_concurrency: maxConcurrency, generator_backpressure: generatorBackpressure,
      timing_boundary: 'Fabric SDK call through valid commit, or evaluated query response; excludes REST/IPFS/AI' } }));
})().catch((err) => { console.error(String(err)); process.exitCode = 1; }).finally(() => { gateway.close(); client.close(); });

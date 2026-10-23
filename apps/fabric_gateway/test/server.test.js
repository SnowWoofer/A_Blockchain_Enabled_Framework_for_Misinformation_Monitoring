'use strict';

// Runs without a Fabric network: requests that pass validation fail later at
// identity loading, which is enough to prove the guards in front of it.
process.env.INTERNAL_API_TOKEN = 'test-internal-token';
process.env.CRYPTO_PATH = '/nonexistent';
process.env.ORGS = 'org1,org2';

const test = require('node:test');
const assert = require('node:assert');
const { app, parseCall } = require('../src/server');

let base;
const server = app.listen(0);
test.before(() => { base = `http://127.0.0.1:${server.address().port}`; });
test.after(() => server.close());

function post(path, body, token = 'test-internal-token') {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['X-Internal-Token'] = token;
  return fetch(base + path, { method: 'POST', headers, body: JSON.stringify(body) });
}

test('rejects calls without the internal token', async () => {
  const res = await post('/invoke', { org: 'org1', function: 'Submit', args: [] }, null);
  assert.strictEqual(res.status, 401);
});

test('rejects calls with a wrong token', async () => {
  const res = await post('/query', { org: 'org1', function: 'QueryAllReports' }, 'nope');
  assert.strictEqual(res.status, 401);
});

test('only allow-listed functions, and reads only through /query', async () => {
  assert.strictEqual((await post('/invoke', { org: 'org1', function: 'QueryAllReports' })).status, 400);
  assert.strictEqual((await post('/query', { org: 'org1', function: 'Submit' })).status, 400);
  assert.strictEqual((await post('/invoke', { org: 'org1', function: 'org.hyperledger.fabric:GetMetadata' })).status, 400);
});

test('validates role, org and args', () => {
  const fns = new Set(['QueryReport']);
  assert.match(parseCall({ function: 'QueryReport', role: 'admin' }, fns).error, /role/);
  assert.match(parseCall({ function: 'QueryReport', org: '../etc' }, fns).error, /org/);
  assert.match(parseCall({ function: 'QueryReport', args: 'x' }, fns).error, /args/);
  assert.match(parseCall({ function: 'QueryReport', args: ['x'.repeat(5000)] }, fns).error, /too long/);
  assert.deepStrictEqual(parseCall({ function: 'QueryReport', org: 'org2', role: 'fact_checker', args: [7] }, fns),
    { org: 'org2', role: 'fact_checker', fn: 'QueryReport', args: ['7'] });
});

test('a valid call reaches identity loading with the identity for its role', async () => {
  // register-roles.sh enrols fact_checkerN / officialN; the request's role picks which one signs
  let res = await post('/query', { org: 'org1', role: 'fact_checker', function: 'QueryAllReports' });
  assert.strictEqual(res.status, 500);
  assert.match((await res.json()).error, /users\/fact_checker1@org1\.example\.com\//);
  res = await post('/invoke', { org: 'org2', role: 'official', function: 'FinalizeReport', args: ['r1'] });
  assert.match((await res.json()).error, /users\/official2@org2\.example\.com\//);
});

test('health stays open and lists no secrets', async () => {
  const res = await fetch(base + '/health');
  assert.strictEqual(res.status, 200);
  const body = await res.json();
  assert.ok(!JSON.stringify(body).includes('test-internal-token'));
  assert.deepStrictEqual(body.roles, ['official', 'fact_checker']);
});

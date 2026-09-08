# API Reference

Every HTTP endpoint across the seven services, what it does, and what it
expects. Ports are the host-side ports from each service's `docker-compose`
file; inside `kafka_net` the Python services all listen on `:8000`.

Two services require authentication (`X-API-Key`), and both accept the *same*
org-level keys written by `blockchain/scripts/bootstrap-keys.sh` — `key-org1`,
`key-org2`, and so on. There is no user/account layer: an organisation
authenticates as itself, and which person inside that organisation is making
the call is that organisation's own concern.

| Service | Port | Auth | Role |
|---|---|---|---|
| [claim-ingest-worker](#claim-ingest-worker--8003) | 8003 | none | test harness: pushes claims onto `claims.raw` |
| [flagging-engine](#flagging-engine--8004) | 8004 | none | the model; consumes `claims.raw`, produces `claims.inferenced` |
| [submission-worker](#submission-worker--8001) | 8001 | none | consumes `claims.inferenced`, registers claims on-chain |
| [blockchain_gateway](#blockchain_gateway--8000) | 8000 | **X-API-Key** | the ledger + IPFS API; everything on-chain goes through here |
| [fact-checking-service](#fact-checking-service--8002) | 8002 | **X-API-Key** | the org-facing API fact-checkers actually use |
| [fabric_gateway](#fabric_gateway--9100) | 9100 | none¹ | Fabric Gateway SDK sidecar |
| [ipfs_gateway](#ipfs_gateway--9101) | 9101 | none¹ | generic IPFS add/cat bridge |

¹ Internal sidecars. They are not intended to be exposed outside the host, and
apply no authorisation of their own — anything that can reach them can invoke
chaincode or write to IPFS as any configured identity.

---

## claim-ingest-worker — `:8003`

A stand-in for real ingestion (scraping, a feed subscription), so the rest of
the pipeline can be exercised end to end. Not part of the framework's claims.

### `GET /health`
Liveness. `{"status": "ok"}` once the Kafka producer is connected, `"starting"`
before that.

### `POST /ingest`
Queues one or more claim texts onto `claims.raw`.

```json
{"claims": ["<claim text>", "..."]}
```

Mints an `ingest_id` per claim as `<source_platform>_<uuid4>` and emits
`{ingest_id, source_platform, content, ingest_timestamp}`. Returns the ids so
the caller can correlate:

```json
{"queued": 2, "ingest_ids": ["test_data_4039de7f…", "test_data_1b335e2d…"]}
```

The ids are random, not derived from content — the same text submitted twice
gets two different ids, and nothing can recompute one.

---

## flagging-engine — `:8004`

The model. Its normal work is via Kafka, not HTTP: it consumes `claims.raw`,
scores each claim, and publishes to `claims.inferenced`. The endpoints below
are for direct scoring and observability.

### `GET /health`
Liveness plus whether the model has finished loading.

### `POST /predict`
Score one piece of text directly, bypassing Kafka.

```json
{"post_text": "<text>"}
```

```json
{
  "label": "misinformation",
  "label_binary": "1",
  "confidence": 0.999955,
  "misinformation_probability": 0.999955,
  "model_version": "afro-xlmr-large-76L-misinfo-v1"
}
```

`label` is the model's argmax, and `confidence` is the probability of *that*
class — so it is always ≥ 0.5 and can never contradict the label.
`label_binary` is the ledger's domain (`"0"`/`"1"`), emitted here because only
this service knows its model's class ordering.
`misinformation_probability` spans the full [0, 1] range and is the sortable
triage signal; `confidence` is not usable for ranking, because a confidently
benign claim and a confidently harmful one both score near 1.0.

### `GET /metrics`
Prometheus exposition. Includes `flagging_engine_requests_total`,
`flagging_engine_misinformation_total`, inference latency, queue wait, and
batch size histograms.

---

## submission-worker — `:8001`

Consumes `claims.inferenced` and calls the gateway's `POST /api/reports` for
each claim. It has no request API — these two endpoints exist only for health
and observability. A claim it cannot submit is appended to a local dead-letter
file rather than dropped.

### `GET /health`
Liveness.

### `GET /metrics`
Prometheus exposition: `claims_registered_total`,
`claims_registration_failed_total`, and the timestamp of the last successful
write.

---

## blockchain_gateway — `:8000`

The ledger and IPFS API. Assembles claim documents, computes `inference_hash`,
publishes to IPFS, and invokes chaincode. **All endpoints require
`X-API-Key`**, and the key determines which org signs the transaction.

### `GET /api/status`
Off-chain storage backend status — whether IPFS is reachable, and which
backend is in use (`ipfs`, or `sqlite` when degraded).

### `POST /api/reports`
Register a claim. Called by the submission-worker, once per claim.

```json
{
  "ingest_id": "test_data_4039de7f…",
  "label": "1",
  "confidence": 0.999955,
  "model_version": "afro-xlmr-large-76L-misinfo-v1",
  "content": "<claim text>",
  "source_platform": "test_data",
  "published_at": "2026-09-07T09:14:22Z",
  "inference_timestamp": "2026-09-07T09:14:23.184627+00:00"
}
```

The gateway nests these into `source` and `inference`, computes
`inference_hash` over exactly those two subtrees, publishes the document to
IPFS, and anchors the resulting CID on-chain.

```json
{"root_cid": "Qm…", "inference_hash": "18f09cb4…"}
```

`root_cid` is the v1 CID and becomes the ledger key. It never changes.

### `GET /api/reports`
List every on-chain record. Optional `?status=PENDING|UNDER_REVIEW|REOPENED|FINAL|EXPIRED`
filters client-side after fetching all of them.

### `GET /api/reports/{root_cid}`
The full off-chain document, fetched from IPFS at whichever version the ledger
currently points to, with `root_cid`, `current_cid` and `previous_cid` added so
the caller can walk the version chain.

### `GET /api/reports/{root_cid}/chain`
The on-chain record only: `inference_hash`, `current_cid`, `inference_label`,
`confidence`, `status`, `round`, `required_panel`, `fact_checks[]`,
`reopens[]`, and `final_label` once settled.

### `POST /api/reports/{root_cid}/fact-check`
Submit one fact-check. **This is also how a claim gets finalised** — there is
no separate finalise call.

```json
{"outcome": "0" | "1", "reasoning": "<free text>", "support": ["<url>", "..."]}
```

The gateway publishes a new document version carrying the reasoning and
supporting links, then invokes the chaincode with that version's CID. The
chaincode records the outcome and, if the round has reached its panel with a
two-thirds supermajority, closes the claim in the same transaction.

Still open:
```json
{"ok": true, "current_cid": "Qm…", "status": "UNDER_REVIEW",
 "round": 0, "required_panel": 3, "checks_this_round": 2}
```

Settled — the gateway publishes one further version carrying the verdict and
advances the pointer:
```json
{"ok": true, "current_cid": "Qm…", "status": "FINAL", "final_label": "1"}
```

Rejected when: the claim is not open, the org already checked *in this round*,
or `outcome` is not `"0"`/`"1"`.

### `POST /api/reports/{root_cid}/reopen`
Reopen a `FINAL` claim at a panel two larger. Any registered org may reopen;
no body.

```json
{"ok": true, "status": "REOPENED", "round": 1, "required_panel": 5,
 "standing_label": "1", "deadline": "2026-09-10T…",
 "reopens": [{"org_msp": "Org4MSP", "round": 1, "at": "…", "tx_id": "…"}]}
```

`standing_label` is the existing verdict, and it is deliberately **not
cleared** — reopening raises a question without withdrawing the consortium's
answer while that question is open.

Rejected when: the claim is not `FINAL`, this org has already reopened it, the
claim has been reopened three times, or the next panel would exceed the number
of registered orgs.

### `POST /api/reports/{root_cid}/expire`
Close out a claim past its fact-check deadline. A `PENDING`/`UNDER_REVIEW`
claim becomes `EXPIRED`. A `REOPENED` claim returns to `FINAL` with its
standing verdict **reaffirmed** — a challenge that cannot gather its panel
fails, rather than leaving the claim in limbo.

### `GET /api/reports/{root_cid}/verify`
Tamper-evidence check.

```json
{"root_cid": "Qm…", "off_chain_intact": true, "matches_on_chain": true,
 "verified": true, "explanation": "…"}
```

`off_chain_intact` recomputes the document's hash from its own contents;
`matches_on_chain` compares that to the anchored value. Both must hold.

### `GET /api/reports/{root_cid}/history`
Every transaction that ever wrote this claim's ledger key, via Fabric's
`GetHistoryForKey`, **sorted oldest first** — so `[0]` is the submission. Each
entry carries `tx_id`, `timestamp`, `is_delete`, and the full record as it
stood at that moment.

### Organisation admission

| Endpoint | Purpose |
|---|---|
| `POST /api/orgs/apply` | Issue an API key for a new org (body: `{"org": "org6"}`) |
| `POST /api/orgs/{msp}/admission` | Open an admission request for a candidate MSP |
| `POST /api/orgs/{msp}/admission/vote` | Vote on admission (body: `{"verdict": "0"｜"1"}`) |
| `POST /api/orgs/{msp}/admission/finalize` | Admit if ~⅔ of registered orgs voted yes |
| `GET /api/orgs/{msp}/admission` | Current admission request and its votes |
| `GET /api/orgs` | Registered orgs and when each was registered |

Admission deliberately uses a **scaling** two-thirds supermajority of the whole
consortium, unlike fact-checking's fixed small panel: admitting a permanent
member is a governance decision, not a per-claim verdict.

---

## fact-checking-service — `:8002`

The API a fact-checking organisation actually uses. A thin, org-facing layer
over the gateway that adds a work queue and joins on-chain records to their
readable off-chain content. **Requires `X-API-Key`.**

### `GET /health`
Liveness.

### `GET /claims/pending`
The caller's work queue: claims that are still open (`PENDING`,
`UNDER_REVIEW`, `REOPENED`) **and that this org has not checked in the current
round**. Each is enriched with its off-chain content, since a fact-checker
cannot fact-check a hash.

The per-round filter matters: after a reopen, an org that checked in round 0
may — and should — check again, so filtering on the whole history would hide
exactly the claims that most need re-examination.

There is no assignment or locking. Orgs pick from the queue, and the chaincode
rejects a submission if the claim closed meanwhile. Closed claims leave the
queue and everyone moves on.

### `GET /claims/{root_cid}`
One claim: the on-chain record joined to its readable off-chain document.

### `POST /claims/{root_cid}/fact-check`
Submit a finding.

```json
{"outcome": "0" | "1", "reasoning": "<required, non-empty>", "support": ["<url>"]}
```

Validates `outcome` at this boundary and returns `400` with a readable message
rather than passing an invalid value down to the chaincode. Otherwise
identical in effect to the gateway endpoint, including automatic finalisation.

### `POST /claims/{root_cid}/reopen`
Reopen a finalised claim. Same semantics and same guards as the gateway
endpoint.

---

## fabric_gateway — `:9100`

Node sidecar wrapping the official `@hyperledger/fabric-gateway` SDK. Holds the
signing identity for each org and is the only component that talks to peers.
Internal — not for external exposure.

### `GET /health`
Reports the channel, chaincode name, target peer, and which org identities are
loaded.

### `POST /invoke`
Submit a transaction and wait for commit.

```json
{"org": "org1", "channel": "mychannel", "chaincode": "misinformation",
 "function": "SubmitFactCheck", "args": ["<root_cid>", "1", "<new_cid>"]}
```

Returns `{"ok": true, "status": "SUCCESS", "tx_id": "…"}`. Note it returns the
**transaction id, not the chaincode's return payload** — a caller needing the
resulting state must query for it.

### `POST /query`
Evaluate a read-only transaction. Same body shape; returns the decoded result.

---

## ipfs_gateway — `:9101`

A deliberately generic IPFS bridge — it knows nothing about claims, reports or
hashing. Internal.

### `GET /health`
Whether the IPFS node is reachable, and its version.

### `POST /add`
Stores the raw request body in IPFS and returns its CID. No interpretation of
the bytes.

### `GET /cat/{cid}`
Returns the raw bytes for a CID, exactly as stored.

---

## Status values

| Status | Meaning |
|---|---|
| `PENDING` | Registered, no fact-checks yet |
| `UNDER_REVIEW` | At least one fact-check, panel not yet satisfied |
| `FINAL` | Settled — `final_label` holds the consortium's verdict |
| `REOPENED` | Under re-examination at a larger panel; the previous verdict still stands |
| `EXPIRED` | Deadline passed without reaching a panel |

## Identifiers

| Field | Identifies | Changes? |
|---|---|---|
| `ingest_id` | this ingestion event | never |
| `inference_hash` | the claim as scored by this model | never |
| `root_cid` | the v1 document — the ledger key | never |
| `current_cid` | the live document version | on every re-publish |
| `previous_cid` | the version this one supersedes | per version |
| `tx_id` | the transaction that *created* the thing carrying it | never |

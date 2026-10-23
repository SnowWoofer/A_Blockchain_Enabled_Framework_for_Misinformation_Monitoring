# Security model

This document describes what the prototype protects, against whom, how, and
where it stops. It covers the deployment produced by `./startup.sh` +
`docker compose up` (or `./demo.sh` for the Fabric-less demo).

## Assets

| Asset | Why it matters |
|-|-|
| Org signing identities (Fabric X.509 keys) | Whoever holds them can vote on the ledger as that organisation |
| The consensus outcome | A verdict published as "misinformation" must reflect a genuine 2/3 consortium decision |
| Claim content and fact-check reasoning (IPFS) | Must match what was anchored on-chain; tampering must be detectable |
| Fact-checker identities | Fact-checkers are harassment targets; individuals must not be exposed publicly |
| Public tipsters | Must be able to report anonymously |
| User credentials and sessions | Grant the power to vote or administer an org |

## Threat actors

1. **Anonymous internet users**: browse the portal, submit tips, may try
   flooding, injection, XSS, credential stuffing.
2. **A compromised or careless fact-checker account**: phished password,
   stolen session, or an insider voting carelessly.
3. **A member organisation acting in bad faith**: tries to confirm its own
   claims, cut a vote short, or rewrite history.
4. **A process on the host or the Docker network**: reaches internal ports
   (sidecar, Kafka, IPFS RPC) and tries to bypass the gateway.

## Architecture and trust boundaries

```
 browser ──HTTPS──▶ web-client edge (nginx, :8443)          ◀── the only public port
                      │  TLS, CSP/HSTS headers, per-IP rate limits, 64 KiB bodies
                      ├─ /api/*  ─▶ blockchain-gateway   (authn/authz, audit, validation)
                      ├─ /fc/*   ─▶ fact-checking-service (holds no credentials)
                      └─ /tips   ─▶ claim-ingest-worker   (anonymous, rate-limited)
 blockchain-gateway ──X-Internal-Token──▶ fabric-gateway sidecar ──mTLS gRPC──▶ Fabric peers
                    ──X-Internal-Token──▶ ipfs-gateway ──▶ private IPFS swarm (swarm.key)
 claim-ingest ──HMAC(raw key)──▶ Kafka ──▶ flagging-engine ──HMAC(flagged key)──▶ Kafka ──▶ submission-worker
```

Everything except the edge (8443, plus 8088 which only redirects) and the
IPFS swarm port (4001, gated by the private swarm key) is published on
`127.0.0.1` only. Services reach one another over the `misinfo_net` Docker
network by name.

## Controls

### On-chain (chaincode `misinformation`, the trust anchor)

* **ABAC**: every write checks the `role` attribute in the caller's X.509
  certificate (`official` / `fact_checker`).
* **Conflict of interest**: the organisation that submitted a report cannot
  fact-check it, so the AI stakeholder org cannot confirm its own flags.
* **Quorum cannot be cut short**: `FinalizeReport` (simple majority of votes
  cast) is only available after the 72 h window. While voting is open, only
  the 2/3 rule in `SubmitFactCheck` can reach `FINAL`, so a single official
  can no longer finalise with 2 of N votes.
* **Exact early rejection**: "remaining voters" counts only eligible orgs (not
  the submitter, not those who voted), so settled reports close immediately
  instead of hanging until expiry.
* One vote per org per report; finalised reports are immutable; full history
  via `GetHistoryForKey`.

### Gateway (authentication, authorisation, audit)

* **Individual accounts** (`AUTH_MODE=jwt`, default): usernames scoped to one
  org with a role (`official`, `fact_checker`, `observer`). bcrypt (cost 12),
  NIST 800-63B-style policy (12+ chars, blocklist, no username), lockout after
  5 failures, timing-equalised failed logins, per-IP and per-username login
  rate limits.
* **Optional TOTP 2FA** (RFC 6238, replay-protected), strongly recommended for
  officials. Officials can reset a user's access: new one-time password, 2FA
  cleared, sessions ended.
* **Sessions**: 15-minute JWT access tokens held only in browser memory;
  8-hour rotating refresh tokens in an `HttpOnly; Secure; SameSite=Strict`
  cookie scoped to `/api/auth`, plus a required `X-Requested-With` header
  (CSRF). Replaying a rotated refresh token revokes the session (20 s grace
  for concurrent tabs). Logout, disabling, role changes and password changes
  take effect immediately.
* **One-time passwords** for every new or reset account; a forced password
  change blocks every other route until it is done.
* **Role checks on every route, mirrored onto Fabric identities**: the gateway
  signs each ledger call with the caller's own role identity (`officialN` or
  `fact_checkerN`), so chaincode ABAC sees the real role. Previously every call
  signed as `official`.
* **API keys** for machine clients, stored as SHA-256 hashes (existing
  plaintext keys are migrated automatically), each with an org and a role. The
  submission worker uses an `ai_submitter` key that can only submit reports.
  `POST /api/orgs/apply` mints random keys and is official-only (it used to
  let any org mint a key for any other org).
* **Validation**: strict schemas (labels, lengths, at most 10 evidence links
  that must be absolute http(s) URLs with no credentials), report/MSP ID
  formats, 256 KiB body cap, security headers, CORS closed by default.
* **Tamper-evident audit log**: logins and failures, lockouts, denied
  requests, votes, governance actions, account changes, key issuance. Each
  entry hash-chained to the previous one; `GET /api/audit/verify` (and
  `manage.py verify-audit`) detects edits or deletions.
* **Pseudonymous reviewers**: off-chain fact-check entries carry an HMAC
  pseudonym (`reviewer_ref`), so an org can trace its own reviewers while the
  public sees only the organisation.
* **Public API exposes closed reports only**: unverified claims are never
  published (so the portal cannot amplify them), and results are cached
  briefly so public traffic cannot become full-ledger scans.

### Service-to-service

* **Fabric Gateway sidecar** (holds every org's keys): requires
  `X-Internal-Token`, refuses to start without it, allow-lists the contract's
  functions (reads only via `/query`), validates org/role/args, loopback only.
* **IPFS gateway**: `/add` requires the internal token and a size cap; `/cat`
  validates CIDs (blocks query-string injection into kubo); kubo RPC and HTTP
  gateways are loopback only; the swarm is private.
* **Pipeline integrity**: claims are HMAC-signed per hop with separate keys,
  so the public-facing ingest worker cannot forge an AI verdict on
  `claims.flagged`. Consumers drop anything unsigned or altered. The
  submission worker de-duplicates by `msg_id`, so replays and redeliveries do
  not create duplicate on-chain reports.
* **Fact-checking service** holds no credentials: it forwards each caller's
  own token, so the gateway authorises the actual person. (It used to hold
  every org's API key.) It also enforces **blind review**: other orgs'
  verdicts are hidden until your org has voted.

### Edge and containers

* TLS 1.2/1.3 only, modern ciphers, strict CSP (`script-src 'self'`, no inline
  code in the build), `frame-ancestors 'none'`, `nosniff`, `no-referrer`,
  restrictive Permissions-Policy; HSTS configurable (`HSTS_MAX_AGE`).
* Per-IP rate limits: sign-in 10/min, tips 6/min, API 30/s; connection caps;
  internal paths (`/docs`, `/openapi.json`, `/metrics`) not exposed.
* The tip route is excluded from access logs, and backends never see a client
  IP the edge did not set (`X-Real-IP` overwritten, `X-Forwarded-For` cleared).
* Edge runs as an unprivileged user, read-only root filesystem, all
  capabilities dropped, `no-new-privileges`. Stateless Python services run as
  a non-root user; the gateway runs as the host user that owns its data.
* Secrets are generated per deployment (`blockchain/scripts/gen-secrets.sh`)
  into a 0600 `.env` and `secrets/` (git-ignored); compose files refuse to
  start without them. Grafana anonymous access is off; Prometheus, Grafana and
  Explorer are loopback only.

## Known limitations

These are deliberate scope limits of a research prototype, not oversights:

* **Single-host custody.** One gateway and one sidecar hold every org's Fabric
  identities. In a real consortium each organisation runs its own gateway with
  only its own keys (ideally in an HSM), and per-user Fabric enrolment would
  put individual identities on-chain.
* **Blind review is a workflow property.** Any member can still query the
  ledger directly. True vote secrecy would need a commit-reveal scheme.
* **Internal traffic is not encrypted** on the Docker network (Kafka
  PLAINTEXT, sidecar HTTP). Messages are authenticated (HMAC, shared token) but
  not confidential. Production would add Kafka SASL/TLS and mTLS between
  services.
* **Rate limiters and the audit log are per process.** The audit log is
  tamper-evident, not tamper-proof: someone with write access to the file
  could rebuild the chain. Anchoring its head hash on the ledger periodically
  would close that gap.
* **Benchmark mode keeps well-known API keys** (`key-orgN`, `stress-key`) so
  the research harnesses work unchanged. Use `./startup.sh --secure` for
  anything beyond local benchmarking.
* **The IPFS swarm key was previously committed to git.** It is now
  untracked, but anyone with the repository history has it. Rotate it: delete
  `blockchain/scripts/swarm.key` and re-run `blockchain/scripts/ipfs-private-swarm.sh`.
* **Explorer** still uses its default credentials (loopback-only now).
* **Self-signed certificate** by default. Replace `secrets/tls/server.{crt,key}`
  with a real certificate and set `HSTS_MAX_AGE` for a real deployment.

# Simulations

One file per scenario. Each drives a **single claim** of the framework against
the running system and exits non-zero if it does not hold — so these check
behaviour rather than narrate it.

Every scenario is self-contained: it creates its own claim and drives it to
whatever state it needs. They can run in any order, individually, or
repeatedly, and none depends on another having run first.

```bash
python3 simulations/scenario_5.py     # one scenario
python3 simulations/run_all.py        # all eight, with a tally
go run simulations/consensus_cases.go # the rules, offline
```

## Prerequisites

**Five organisations.** Scenarios 5–8 exercise reopening, which needs a panel
of 5 in round 1 — a three-org network cannot demonstrate it and the chaincode
will correctly refuse.

```bash
./startup.sh --orgs 5 --skip-caliper   # Fabric, IPFS, gateways, keys
docker compose up -d --build           # Kafka + the pipeline
```

## The scenarios

| File | Claim under test | Checks |
|---|---|---|
| `scenario_1.py` | A claim reaches the ledger with its verdict attached, hashed and anchored — registered whatever the model concluded | 11 |
| `scenario_2.py` | Fact-checkers find work through the pending queue, with no assignment or locking | 7 |
| `scenario_3.py` | A claim closes on two-thirds of a three-org panel; no tie is possible | 12 |
| `scenario_4.py` | Every rejection the ledger owns arrives as a readable message, not a nested transport error | 13 |
| `scenario_5.py` | Reopening escalates the panel and holds the standing verdict throughout | 11 |
| `scenario_6.py` | Two orgs close a claim quickly; it takes four to overturn them | 15 |
| `scenario_7.py` | Every action is attributable on the record, without reconstructing block history | 14 |
| `scenario_8.py` | One hash survives four document versions, and an outsider can recompute it | 12 |

`harness.py` holds the shared plumbing — HTTP calls, claim creation, the
`check()` assertion, and the pass/fail tally. It is not a scenario.

## `consensus_cases.go`

The consensus and escalation arithmetic, lifted out of the chaincode and run
with no network at all. Covers the supermajority at each panel size, that odd
panels cannot tie, that only the current round is counted, escalation bounds
for consortiums of 3 to 100 orgs, and that one actor cannot spend the whole
reopen budget.

It duplicates logic from `misinformation.go`. If the chaincode changes and
this does not, it proves nothing — that is the cost of testing chaincode
without a Fabric test harness, and it is worth knowing rather than assuming.

This is where two escalation bugs were caught before they ever ran: reopening
was impossible on a three-org network, and a hundred-org consortium allowed
thirty-two rounds of it.

## Reading a failure

`check()` prints one line per assertion and the tally at the end. If
`checks_this_round` stalls below `required_panel`, an org could not
authenticate — confirm `fact-checking-service`'s `ORGS` covers every org being
driven. If a scenario cannot reach the gateway at all it exits immediately
rather than reporting dozens of downstream failures.

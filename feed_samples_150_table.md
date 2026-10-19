# Feed-sample benchmark: 150 samples by consortium size

Means are in milliseconds. “Pending” means a 150-sample run has not yet been collected; it is not an estimate.

| Consortium size (orgs) | Sample size | Mean ingest → committed claim (ms) | Mean verification (ms) | Mean ingest → final verdict (ms) | Successful / failed |
|---:|---:|---:|---:|---:|---:|
| 3 | 150 | Pending 150-sample run | Pending 150-sample run | Pending 150-sample run | Pending |
| 5 | 150 | Pending 150-sample run | Pending 150-sample run | Pending 150-sample run | Pending |
| 10 | 150 | 2413.322 | 78.714 | 13163.842 | 150 / 0 |
| 15 | 150 | 2435.568 | 95.488 | 19759.058 | 150 / 0 |
| 20 | 150 | 2431.033 | 98.469 | 24093.229 | 150 / 0 |

The completed 10-, 15-, and 20-org runs used LevelDB, seed 100, and each verified 150/150 samples. The 3- and 5-org 150-sample runs are still needed to complete this comparison.

Latency definitions: ingest-to-committed-claim ends when the claim is committed; verification is the evidence/ledger integrity check; ingest-to-final-verdict ends when the final reviewer decision is available. These timings are end-to-end workflow metrics, not Caliper raw throughput.

# Caliper benchmark results — 19 October 2026

LevelDB, Hyperledger Fabric 2.5.16, 2 Caliper workers. Each configuration ran the requested count of report writes followed by the same count of full-scan reads. Values below are Caliper’s reported round results; latencies are seconds, rates are transactions per second. The workload count is per round, not a transaction count averaged across read and write.

All 25 requested org-count/count configurations completed with passed status. Every write and read round reported zero failures. The runner also checked the per-worker attempt counts and the expected report-count change.

The target set was 3, 5, 10, 15, 20 organisations × 100, 250, 500, 1,000, 2,000 transactions. For repeated org/count runs, the latest completed run was selected. The extra 3-org/200 and 3-org/4,000 runs are excluded.

## Write rounds

| Orgs | Sample size (transactions/round) | Success | Fail | Send rate (TPS) | Max latency (s) | Min latency (s) | Avg latency (s) | Throughput (TPS) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 3 | 100 | 100 | 0 | 170.6 | 0.46 | 0.07 | 0.24 | 103.7 |
| 3 | 250 | 250 | 0 | 150.7 | 0.64 | 0.08 | 0.38 | 137.1 |
| 3 | 500 | 500 | 0 | 161.0 | 0.76 | 0.08 | 0.40 | 145.1 |
| 3 | 1000 | 1000 | 0 | 176.9 | 0.63 | 0.08 | 0.36 | 172.7 |
| 3 | 2000 | 2000 | 0 | 188.1 | 0.65 | 0.08 | 0.36 | 185.1 |
| 5 | 100 | 100 | 0 | 147.5 | 0.48 | 0.09 | 0.27 | 91.9 |
| 5 | 250 | 250 | 0 | 174.3 | 0.53 | 0.08 | 0.35 | 135.9 |
| 5 | 500 | 500 | 0 | 166.0 | 0.59 | 0.08 | 0.37 | 155.2 |
| 5 | 1000 | 1000 | 0 | 170.8 | 0.69 | 0.09 | 0.37 | 168.0 |
| 5 | 2000 | 2000 | 0 | 180.7 | 0.63 | 0.08 | 0.36 | 177.1 |
| 10 | 100 | 100 | 0 | 145.3 | 0.54 | 0.09 | 0.32 | 85.0 |
| 10 | 250 | 250 | 0 | 125.8 | 0.83 | 0.10 | 0.51 | 105.5 |
| 10 | 500 | 500 | 0 | 152.5 | 0.70 | 0.09 | 0.42 | 136.6 |
| 10 | 1000 | 1000 | 0 | 152.8 | 0.71 | 0.10 | 0.41 | 148.1 |
| 10 | 2000 | 2000 | 0 | 113.8 | 3.27 | 0.09 | 0.48 | 111.6 |
| 15 | 100 | 100 | 0 | 122.1 | 0.55 | 0.10 | 0.34 | 80.1 |
| 15 | 250 | 250 | 0 | 122.2 | 0.80 | 0.10 | 0.49 | 108.6 |
| 15 | 500 | 500 | 0 | 126.5 | 0.85 | 0.10 | 0.48 | 122.2 |
| 15 | 1000 | 1000 | 0 | 132.2 | 0.87 | 0.10 | 0.47 | 128.7 |
| 15 | 2000 | 2000 | 0 | 136.0 | 0.91 | 0.11 | 0.47 | 134.0 |
| 20 | 100 | 100 | 0 | 125.8 | 0.66 | 0.14 | 0.43 | 73.9 |
| 20 | 250 | 250 | 0 | 96.0 | 0.99 | 0.12 | 0.64 | 87.2 |
| 20 | 500 | 500 | 0 | 96.8 | 1.27 | 0.10 | 0.62 | 95.0 |
| 20 | 1000 | 1000 | 0 | 82.1 | 1.87 | 0.12 | 0.87 | 78.3 |
| 20 | 2000 | 2000 | 0 | 75.8 | 1.76 | 0.13 | 0.89 | 74.8 |

## Read rounds

| Orgs | Sample size (transactions/round) | Success | Fail | Send rate (TPS) | Max latency (s) | Min latency (s) | Avg latency (s) | Throughput (TPS) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 3 | 100 | 100 | 0 | 333.3 | 0.23 | 0.02 | 0.17 | 236.4 |
| 3 | 250 | 250 | 0 | 165.6 | 0.69 | 0.04 | 0.36 | 160.7 |
| 3 | 500 | 500 | 0 | 107.0 | 1.21 | 0.07 | 0.64 | 103.6 |
| 3 | 1000 | 1000 | 0 | 60.9 | 1.97 | 0.08 | 1.09 | 59.1 |
| 3 | 2000 | 2000 | 0 | 31.9 | 3.89 | 0.19 | 2.15 | 31.7 |
| 5 | 100 | 100 | 0 | 398.4 | 0.11 | 0.01 | 0.05 | 380.2 |
| 5 | 250 | 250 | 0 | 278.1 | 0.35 | 0.03 | 0.19 | 254.6 |
| 5 | 500 | 500 | 0 | 150.8 | 0.84 | 0.04 | 0.40 | 141.5 |
| 5 | 1000 | 1000 | 0 | 67.5 | 1.75 | 0.08 | 0.92 | 66.6 |
| 5 | 2000 | 2000 | 0 | 34.7 | 3.33 | 0.17 | 1.96 | 34.1 |
| 10 | 100 | 100 | 0 | 393.7 | 0.12 | 0.02 | 0.06 | 367.6 |
| 10 | 250 | 250 | 0 | 304.9 | 0.34 | 0.03 | 0.16 | 290.4 |
| 10 | 500 | 500 | 0 | 158.5 | 0.77 | 0.04 | 0.40 | 145.3 |
| 10 | 1000 | 1000 | 0 | 73.6 | 1.78 | 0.08 | 0.95 | 69.2 |
| 10 | 2000 | 2000 | 0 | 30.7 | 4.00 | 0.21 | 2.12 | 30.3 |
| 15 | 100 | 100 | 0 | 378.8 | 0.15 | 0.01 | 0.08 | 352.1 |
| 15 | 250 | 250 | 0 | 213.3 | 0.52 | 0.03 | 0.26 | 200.6 |
| 15 | 500 | 500 | 0 | 121.4 | 1.11 | 0.05 | 0.56 | 117.3 |
| 15 | 1000 | 1000 | 0 | 52.8 | 2.19 | 0.10 | 1.19 | 52.3 |
| 15 | 2000 | 2000 | 0 | 21.1 | 6.89 | 0.23 | 3.11 | 20.8 |
| 20 | 100 | 100 | 0 | 353.4 | 0.19 | 0.02 | 0.11 | 306.7 |
| 20 | 250 | 250 | 0 | 202.6 | 0.65 | 0.03 | 0.30 | 184.9 |
| 20 | 500 | 500 | 0 | 101.1 | 1.33 | 0.06 | 0.73 | 87.2 |
| 20 | 1000 | 1000 | 0 | 40.1 | 3.21 | 0.11 | 1.65 | 39.6 |
| 20 | 2000 | 2000 | 0 | 18.2 | 11.98 | 0.20 | 3.34 | 18.2 |

Caliper send rate is the rate at which the load generator submits requests; throughput is the measured completion rate. These measurements are finite counted batches with 2 workers, not an unbounded maximum-throughput search. They exercise chaincode writes and full-scan reads directly, not the AI/Kafka/IPFS end-to-end path.

Each result row can be traced to its `result_json` path in the CSV. The run directories also retain Caliper HTML reports, transaction-level logs, and benchmark/network configuration snapshots.

## Ledger size at read stage

The read round scans the world state after the write round has committed. The initial ledger sizes varied by run; the read stage therefore does not always operate on a common ledger cardinality. `caliper-results.csv` includes each run’s initial report count and report count after writes. Treat read latency comparisons across configurations as workload observations, not a controlled comparison at identical database size. In particular, the selected 3-org/100 run started with 201 reports; the other selected 100-count runs started with 1.

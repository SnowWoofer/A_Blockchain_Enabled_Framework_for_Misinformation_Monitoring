# Caliper

Current setup and both benchmark paths are documented in the [root README](../../README.md). Run `benchmarks/caliper/run-caliper.sh --help` from the repository root for options. The runner generates connection and benchmark settings for the selected deployment and saves unique logs, JSON and HTML reports. The files under `networks/` and `benchmarks/` show reference templates; the runner generates the actual settings per run. Original legacy scripts are archived under `info/ArtyIndo/caliper-legacy/`.

```bash
benchmarks/caliper/run-caliper.sh --count 1000 --workers 2 --load 100
```

This submits 1,000 write attempts shared across the workers, waits for completion,
then performs 1,000 full-scan read attempts. `--name` defaults to `development`;
`--verbose` streams detailed logs. Database and organisation count come from the
existing deployment. Count remainders use Caliper's public message setter through
`workload/count-fixed-load.js`, which delegates pacing to the official fixed-load
controller without modifying the installed Caliper package.

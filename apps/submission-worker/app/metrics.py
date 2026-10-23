from prometheus_client import Counter, Gauge

CLAIMS_REGISTERED_TOTAL = Counter(
    "submission_worker_claims_registered_total", "Total scored claims submitted to the blockchain gateway"
)
CLAIMS_REGISTRATION_FAILED_TOTAL = Counter(
    "submission_worker_claims_registration_failed_total",
    "Total scored claims that failed to submit to the blockchain gateway (written to the dead-letter file instead)",
)
CLAIMS_REJECTED_TOTAL = Counter(
    "submission_worker_claims_rejected_total",
    "claims.flagged messages dropped because their HMAC signature did not verify",
)
CLAIMS_DUPLICATE_TOTAL = Counter(
    "submission_worker_claims_duplicate_total",
    "claims.flagged messages skipped because their msg_id was already submitted",
)
LAST_WRITE_TIMESTAMP = Gauge(
    "submission_worker_last_write_timestamp_seconds", "Unix timestamp of the last successful blockchain submission"
)

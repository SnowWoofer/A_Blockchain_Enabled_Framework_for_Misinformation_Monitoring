from prometheus_client import Counter, Gauge, Histogram

REQUESTS_TOTAL = Counter(
    "flagging_engine_requests_total", "Total inference requests processed"
)
FLAGGED_TOTAL = Counter(
    "flagging_engine_flagged_total", "Total requests flagged as misinformation"
)
INFERENCE_LATENCY = Histogram(
    "flagging_engine_inference_latency_seconds",
    "Model forward-pass latency per batch",
)
QUEUE_WAIT = Histogram(
    "flagging_engine_queue_wait_seconds",
    "Time a request waited in the dynamic batching queue before inference started",
)
BATCH_SIZE = Histogram(
    "flagging_engine_batch_size",
    "Number of requests per inference batch",
    buckets=(1, 2, 4, 8, 16, 32, 64, 128, 256),
)
QUEUE_DEPTH = Gauge(
    "flagging_engine_queue_depth", "Current number of pending requests in the batching queue"
)

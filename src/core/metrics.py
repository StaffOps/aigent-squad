"""Custom business metrics for AIgent-squad using otel-helper."""
from otel_helper import get_meter

meter = get_meter("aigent-squad")

# RED metrics (Request, Error, Duration) per agent
request_counter = meter.create_counter(
    name="aigent.requests.total",
    description="Total requests processed per agent",
    unit="1",
)

error_counter = meter.create_counter(
    name="aigent.errors.total",
    description="Total errors per agent",
    unit="1",
)

request_duration = meter.create_histogram(
    name="aigent.request.duration",
    description="Request processing duration per agent",
    unit="ms",
)

# Token/cost metrics
token_counter = meter.create_counter(
    name="aigent.tokens.total",
    description="Total tokens consumed per agent (input + output)",
    unit="1",
)

estimated_cost = meter.create_counter(
    name="aigent.cost.estimated",
    description="Estimated cost in USD per agent",
    unit="USD",
)

# Cache metrics
cache_hits = meter.create_counter(
    name="aigent.cache.hits",
    description="Cache hits (data cache only)",
    unit="1",
)

cache_misses = meter.create_counter(
    name="aigent.cache.misses",
    description="Cache misses",
    unit="1",
)

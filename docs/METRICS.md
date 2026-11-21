# Prometheus Metrics

[`app/api/metrics.py`](app/api/metrics.py) defines the application's Prometheus metrics.
It does not actively collect data by itself. Instead, it creates named metric objects.
Other parts of the application increment counters or record timings on those objects.
Prometheus later requests `GET /metrics`, and the current values are serialized into
Prometheus's text format.

The application exposes that endpoint in [`app/api/main.py`](app/api/main.py), while
Prometheus is configured to scrape it every 10 seconds in
[`monitoring/prometheus.yml`](monitoring/prometheus.yml).

## Imports

```python
from prometheus_client import Counter, Histogram
```

The `prometheus_client` package provides Python implementations of Prometheus metric
types. This file uses two:

### `Counter`

A counter represents a value that only increases during the lifetime of a process.
Examples include:

- Number of HTTP requests
- Number of cache hits
- Number of LLM tokens
- Number of guardrail blocks

Counters may reset when the application restarts. Prometheus is designed to handle
this and provides functions such as `rate()` and `increase()` to calculate changes
over time.

For example:

```python
HTTP_REQUESTS.labels("GET", "/search", "200").inc()
```

means: add one successful `GET /search` request to the counter.

### `Histogram`

A histogram records observations, usually durations or sizes, into predefined
buckets. This application uses histograms for:

- How long HTTP requests take
- How long individual agent nodes take

A histogram exposes several related time series:

- `_bucket`: number of observations less than or equal to each bucket boundary
- `_count`: total number of observations
- `_sum`: sum of all observed values

For example, the metric `HTTP_LATENCY` produces underlying series similar to:

```text
http_request_duration_seconds_bucket{... le="0.1"} 42
http_request_duration_seconds_bucket{... le="0.25"} 75
http_request_duration_seconds_count{...} 100
http_request_duration_seconds_sum{...} 18.42
```

This allows Prometheus to calculate percentiles such as p95 and p99.

## HTTP metrics

### `HTTP_REQUESTS`

```python
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests",
    ["method", "path", "status"],
)
```

This counts HTTP requests. Its labels are:

- `method`: HTTP method, such as `GET` or `POST`
- `path`: route path, such as `/search`
- `status`: HTTP status code, such as `200`, `404`, or `500`

The middleware records this metric in [`app/api/main.py`](app/api/main.py):

```python
m.HTTP_REQUESTS.labels(
    request.method,
    path,
    str(status),
).inc()
```

For example, a successful search might produce:

```text
http_requests_total{
  method="POST",
  path="/search",
  status="200"
} 1
```

The name ends in `_total` because it is a counter. The Python client and Prometheus
convention both use `_total` for cumulative event counts.

A useful query is:

```promql
sum by (status) (rate(http_requests_total[5m]))
```

This shows the request rate grouped by HTTP status.

### `HTTP_LATENCY`

```python
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP latency",
    ["method", "path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
```

This measures how long HTTP requests take. The value is recorded in seconds. The
labels are `method` and `path`; there is intentionally no `status` label here.
Including status would split latency into separate histograms for every status code,
increasing the number of time series.

The custom buckets are:

```text
10 ms, 25 ms, 50 ms, 100 ms, 250 ms, 500 ms,
1 s, 2.5 s, 5 s, 10 s
```

The middleware measures elapsed time using a monotonic timer:

```python
t0 = time.perf_counter()
...
elapsed = time.perf_counter() - t0
m.HTTP_LATENCY.labels(request.method, path).observe(elapsed)
```

`time.perf_counter()` is appropriate for duration measurement because it is
monotonic and is not affected by system clock adjustments.

A useful p95 query is:

```promql
histogram_quantile(
  0.95,
  sum by (le) (
    rate(http_request_duration_seconds_bucket[5m])
  )
)
```

The middleware uses the route template:

```python
path = route.path if route else "unmatched"
```

This normally records paths such as `/search` or
`/boots/{boot_id}/images/{image_index}`, rather than full URLs. This is important
because using raw URLs could create a separate label value for every boot ID.
Route templates keep label cardinality manageable.

## Search and agent metrics

### `SEARCHES`

```python
SEARCHES = Counter(
    "searches_total",
    "Searches",
    ["mode", "understood_by"],
)
```

This counts search operations. Its labels are:

- `mode`: search mode requested by the client, such as text, image, or hybrid
- `understood_by`: which interpretation path handled the query

It is recorded after the agent runs:

```python
m.SEARCHES.labels(
    req.mode,
    out.get("understood_by", "blocked"),
).inc()
```

If the agent does not provide an `understood_by` value, the label defaults to
`"blocked"`.

Example query:

```promql
sum by (mode, understood_by) (
  rate(searches_total[10m])
)
```

This can show whether users primarily use text, image, or combined search and how
the agent interpreted those requests.

### `NODE_LATENCY`

```python
NODE_LATENCY = Histogram(
    "agent_node_duration_seconds",
    "Agent node latency",
    ["node"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
```

This measures the duration of individual agent graph nodes. The `node` label
identifies an agent step, such as query parsing, retrieval, reranking, answer
generation, or guardrail processing.

The search handler reads timing information from the agent trace:

```python
for step in out.get("trace", []):
    m.NODE_LATENCY.labels(step["node"]).observe(step["ms"] / 1000)
```

The trace stores milliseconds, but Prometheus metrics use seconds, so the code
divides by `1000`. For example, `125` milliseconds becomes `0.125` seconds.

This metric helps distinguish overall request slowness from a specific slow agent
component.

## Cache metrics

```python
CACHE_HITS = Counter("cache_hits_total", "Cache hits")
CACHE_MISSES = Counter("cache_misses_total", "Cache misses")
```

`CACHE_HITS` counts successful cache lookups:

```python
m.CACHE_HITS.inc()
```

`CACHE_MISSES` counts requests that did not find a usable cached result:

```python
m.CACHE_MISSES.inc()
```

Together, they can measure cache effectiveness:

```promql
sum(rate(cache_hits_total[5m]))
/
(
  sum(rate(cache_hits_total[5m]))
  +
  sum(rate(cache_misses_total[5m]))
)
```

These metrics have no labels because they only need one global count each. Adding
unnecessary labels would create more time series without adding useful information.

## Search quality metrics

### `ZERO_RESULTS`

```python
ZERO_RESULTS = Counter(
    "zero_result_searches_total",
    "Searches that returned nothing",
)
```

This counts searches that produce no results. It is recorded when the search is
not blocked and the result list is empty:

```python
if out.get("blocked"):
    m.GUARDRAIL_BLOCKS.inc()
elif not results:
    m.ZERO_RESULTS.inc()
```

The `elif` is significant: a blocked request is not counted as a zero-result search.
A blocked request failed before normal search execution, whereas a zero-result search
completed but found nothing.

This is a product-quality metric, not merely an infrastructure metric. A service can
be operationally healthy while returning poor or irrelevant search results.

For example:

```promql
sum(rate(zero_result_searches_total[15m]))
```

Or, to calculate a zero-result ratio:

```promql
sum(rate(zero_result_searches_total[15m]))
/
sum(rate(searches_total[15m]))
```

### `GROUNDING_FAILURES`

```python
GROUNDING_FAILURES = Counter(
    "answer_grounding_failures_total",
    "LLM answers rejected by output guard",
)
```

This counts answers that failed the application's grounding or faithfulness check.
It is recorded when the agent output explicitly says:

```python
if out.get("grounded") is False:
    m.GROUNDING_FAILURES.inc()
```

The code uses `is False`, rather than merely `if not out.get("grounded")`. That
avoids counting missing or unset values as failures. Only an explicit `False`
indicates that the answer was judged ungrounded.

This is especially important for an LLM-backed application because uptime alone does
not tell you whether the generated answer is trustworthy.

### `GUARDRAIL_BLOCKS`

```python
GUARDRAIL_BLOCKS = Counter(
    "guardrail_blocks_total",
    "Requests blocked by input guard",
)
```

This counts requests rejected by the input guardrail:

```python
if out.get("blocked"):
    m.GUARDRAIL_BLOCKS.inc()
```

It can reveal abuse attempts, unsupported inputs, policy violations, overly
aggressive validation, or a sudden change in user behavior. A sudden increase may
indicate either an attack or an incorrectly configured guardrail.

## LLM usage and cost metrics

### `LLM_TOKENS`

```python
LLM_TOKENS = Counter(
    "llm_tokens_total",
    "LLM tokens",
    ["direction"],
)
```

This counts tokens sent to and received from the language model. The `direction`
label distinguishes `input` and `output`:

```python
m.LLM_TOKENS.labels("input").inc(usage.input_tokens)
m.LLM_TOKENS.labels("output").inc(usage.output_tokens)
```

This produces separate cumulative counters:

```text
llm_tokens_total{direction="input"}
llm_tokens_total{direction="output"}
```

A query for token usage over the previous hour is:

```promql
sum by (direction) (
  increase(llm_tokens_total[1h])
)
```

Because this is a counter, `.inc(amount)` can add more than one at a time.

### `LLM_COST`

```python
LLM_COST = Counter(
    "llm_cost_usd_total",
    "Estimated LLM spend (USD)",
)
```

This tracks estimated cumulative LLM cost in US dollars:

```python
m.LLM_COST.inc(usage.cost_usd)
```

If a request costs `$0.003`, the counter increases by `0.003`. A counter is
appropriate because total spend accumulates over time:

```promql
increase(llm_cost_usd_total[24h])
```

The value is described as estimated because it depends on token accounting and
configured model prices.

### `LLM_ERRORS`

```python
LLM_ERRORS = Counter(
    "llm_errors_total",
    "LLM call errors",
    ["type"],
)
```

This counts errors returned by LLM calls. The `type` label categorizes the error:

```python
for err in usage.errors:
    m.LLM_ERRORS.labels(err).inc()
```

Possible controlled error types include `timeout`, `rate_limit`,
`invalid_response`, and `provider_error`.

A query grouped by error type is:

```promql
sum by (type) (
  rate(llm_errors_total[10m])
)
```

The `type` values should come from a controlled set. Arbitrary exception messages,
request IDs, user input, or stack traces should not be used as label values because
every unique value would create a new Prometheus time series.

## User feedback metric

### `FEEDBACK`

```python
FEEDBACK = Counter(
    "feedback_total",
    "User feedback",
    ["rating"],
)
```

This counts user feedback. The `rating` label is normalized into two values:

```python
m.FEEDBACK.labels(
    "up" if fb.rating > 0 else "down"
).inc()
```

The metric therefore has series such as:

```text
feedback_total{rating="up"}
feedback_total{rating="down"}
```

This is preferable to using arbitrary numeric ratings as label values because the
metric only needs the positive-versus-negative distinction.

A feedback ratio can be calculated with:

```promql
sum(increase(feedback_total{rating="up"}[1h]))
/
sum(increase(feedback_total[1h]))
```

The feedback record itself is stored separately:

```python
state.store.add_feedback(
    fb.request_id,
    fb.rating,
    fb.boot_id,
    fb.comment,
)
```

The Prometheus metric is only an aggregate operational signal; it does not store
individual comments or request details.

## How labels work

A metric declaration with labels creates a metric family. For example:

```python
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests",
    ["method", "path", "status"],
)
```

does not represent just one number. It represents many labeled series:

```text
http_requests_total{method="GET", path="/metrics", status="200"}
http_requests_total{method="POST", path="/search", status="200"}
http_requests_total{method="POST", path="/search", status="422"}
```

The corresponding values are selected with:

```python
HTTP_REQUESTS.labels("POST", "/search", "200")
```

and then changed with:

```python
.inc()
```

For histograms, the same label selection is followed by:

```python
.observe(duration_in_seconds)
```

The label order matters. This:

```python
HTTP_REQUESTS.labels(method, path, status)
```

must match the declaration order:

```python
["method", "path", "status"]
```

## Why the module uses constants

The metrics are declared once at module import time:

```python
HTTP_REQUESTS = ...
HTTP_LATENCY = ...
```

This is important because metric registration should happen once per Python process.
Defining metrics inside a request handler could cause duplicate-registration errors or
unnecessary object creation.

Importing the module gives the rest of the application a shared set of metric
objects:

```python
from app.api import metrics as m
```

Request handlers can then use:

```python
m.CACHE_HITS.inc()
m.SEARCHES.labels(...).inc()
```

The module acts as a central registry and naming location for application metrics.

## How Prometheus gets the values

The `/metrics` endpoint is implemented as:

```python
@router.get("/metrics", tags=["ops"])
def metrics():
    return Response(
        generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
```

`generate_latest()` asks the Prometheus Python client to serialize all registered
metrics. `CONTENT_TYPE_LATEST` sets the correct Prometheus content type so the
scraper knows how to interpret the response.

Prometheus polls this endpoint according to
[`monitoring/prometheus.yml`](monitoring/prometheus.yml):

```yaml
global:
  scrape_interval: 10s
```

The complete flow is:

1. A request enters the API.
2. Middleware records HTTP count and duration.
3. Search logic records cache, agent, quality, and LLM metrics.
4. Prometheus requests `/metrics`.
5. `generate_latest()` returns the current in-process metric values.
6. Prometheus stores timestamped samples.
7. Grafana visualizes and alerts on those samples.

## The four golden signals

The module's opening documentation refers to the standard four golden signals:

| Signal | Metrics in this project |
|---|---|
| Latency | `http_request_duration_seconds`, `agent_node_duration_seconds` |
| Traffic | `http_requests_total`, `searches_total` |
| Errors | HTTP requests with 5xx statuses, `llm_errors_total` |
| Saturation | Primarily supplied by the container or Kubernetes runtime |

The module also adds AI-specific quality and cost signals:

- Cache hit and miss counts
- Zero-result searches
- Grounding failures
- Guardrail blocks
- Token usage
- Estimated LLM cost
- User feedback

This combination is valuable because a search application can appear healthy from an
infrastructure perspective while still having poor retrieval quality, excessive LLM
spending, or ungrounded answers.

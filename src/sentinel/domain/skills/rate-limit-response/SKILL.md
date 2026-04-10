---
name: rate-limit-response
description: Response template for rate-limit and throttling-related support tickets
version: 1.0.0
applies_to: ["rate_limit_*", "throttling"]
---

# Rate Limit Response Template

## 1. Identify the Rate Limit Type

Determine which rate limit the user is hitting from the response headers or error message:

| Header / Error | Limit Type | Description |
|----------------|-----------|-------------|
| `X-RateLimit-Limit` | Per-client request rate | Standard API rate limit |
| `X-RateLimit-Strategy` | Per-strategy quota | Quota allocated to a specific trading strategy |
| `429 Too Many Requests` | Global rate limit | Aggregate limit across all clients |
| `WebSocket: 1008 Policy Violation` | WebSocket connection rate | Too many reconnection attempts |
| `503 Service Unavailable` with `Retry-After` | Circuit breaker | Upstream service protection |

## 2. Standard API Rate Limits

Current rate limits by tier:

| Tier | Requests/sec | Burst Allowance | Typical Use |
|------|-------------|-----------------|-------------|
| Market data (streaming) | 100/s per symbol group | 200/s for 10s | Real-time quote consumption |
| Market data (REST) | 50/s | 100/s for 5s | Historical data queries |
| Order submission | 20/s per strategy | 50/s for 3s | Algorithmic order placement |
| Risk queries | 30/s | 60/s for 5s | Position and exposure lookups |
| Regulatory reporting | 10/s | No burst | Compliance data submission |

Draft response for standard rate limiting:
> You are exceeding the rate limit for your API tier. Current limits and your usage:
>
> - **Your limit**: [X] requests/second
> - **Your current rate**: Check the `X-RateLimit-Remaining` header in your responses
> - **Reset time**: Check the `X-RateLimit-Reset` header for when your quota refreshes
>
> To avoid rate limiting:
> 1. Implement exponential backoff when you receive a `429` response.
> 2. Cache responses for data that does not change within your required freshness window.
> 3. Use WebSocket streaming for real-time data instead of polling REST endpoints.
> 4. Batch requests where the API supports it (e.g., `/quotes/batch` for multiple symbols).

## 3. Market Open/Close Burst Handling

During market open (09:25-09:35 ET) and close (15:55-16:05 ET), traffic spikes are expected.

Draft response for burst-related throttling:
> Rate limits are temporarily elevated during market open and close windows to accommodate increased activity. If you are still being throttled during these periods:
>
> 1. Verify your application is using the burst allowance correctly -- short bursts are permitted, but sustained high rates are not.
> 2. Stagger your initialization requests. Not all symbol groups need to be refreshed simultaneously at market open.
> 3. Pre-warm caches before market open by fetching reference data during pre-market hours.
>
> If your strategy requires higher burst capacity during these windows, submit a capacity request via the API Management Portal with your expected peak QPS and duration.

## 4. Per-Strategy Rate Quotas

Each registered trading strategy receives an individual rate quota to prevent a single strategy from starving others.

Check the strategy's quota allocation:
```
rate_limit.strategy.usage{strategy_id:<id>} by {endpoint}
rate_limit.strategy.rejected{strategy_id:<id>}
```

Draft response:
> Your trading strategy `[strategy-id]` has a dedicated rate quota. This quota is separate from your account-level limit. If your strategy needs a higher allocation:
>
> 1. Review your current usage pattern in the Strategy Dashboard.
> 2. Submit a quota adjustment request with: strategy ID, current allocation, requested allocation, and justification.
> 3. Quota increases require sign-off from the risk management team to ensure fair resource allocation across all strategies.

## 5. WebSocket Reconnection Backoff

Draft response for WebSocket rate limiting:
> Your application is reconnecting to the WebSocket feed too frequently. The connection rate limit is 5 connections per minute per client.
>
> Implement the following reconnection strategy:
> 1. On disconnect, wait 1 second before the first reconnection attempt.
> 2. Double the wait time on each subsequent failure (1s, 2s, 4s, 8s, ...) up to a maximum of 60 seconds.
> 3. Add random jitter (0-500ms) to prevent thundering herd on feed recovery.
> 4. After a successful reconnection, reset the backoff timer.
> 5. Resubscribe to your symbol list incrementally, not all at once.
>
> If the feed is experiencing an outage, check the status page at `https://status.internal` before retrying.

## 6. Regulatory Reporting Endpoint Priority

The regulatory reporting endpoints (`/api/regulatory/*`) have lower rate limits but are given priority during system-wide throttling events. These endpoints are never shed during load shedding.

Draft response for regulatory endpoint issues:
> Regulatory reporting endpoints are rate-limited to 10 requests/second to ensure stable throughput. If you are hitting this limit:
>
> 1. Batch your regulatory submissions using the bulk endpoint (`/api/regulatory/batch`).
> 2. Spread submissions across the reporting window rather than sending all at the deadline.
> 3. If you are experiencing failures (not just rate limiting), escalate immediately -- regulatory reporting has priority support.

## 7. Response Checklist

Before sending any rate-limit response, verify:

- [ ] The specific rate limit tier and current usage are identified
- [ ] The response includes actionable steps to reduce request rate
- [ ] Burst allowances during market open/close are explained if relevant
- [ ] Escalation path for quota increase requests is included
- [ ] Regulatory reporting endpoints are flagged as priority if affected

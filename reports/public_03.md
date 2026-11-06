# Public 03 load report

Measured on Windows, Python 3.13, local FastAPI simulator and SQLite controller. The evaluation target is 4 vCPU / 8 GB Linux; this is a local measurement, not a claim of identical hardware.

| Measure | Result |
|---|---:|
| Simulated days / workflows | 5 / 12 |
| Live events ingested (HTTP chunk metadata agrees with SQLite) | 4,200,115 |
| HTTP NDJSON ingestion throughput (events / ingestion wall time) | 77,273/s |
| End-to-end loop throughput (events / tick wall time) | 20,964/s |
| Mean wall time per simulated day | 40.1 s |
| p95 / max wall time per one-hour tick | 3.96 / 6.41 s |
| p95 / max ingestion time per tick | 1.47 / 2.72 s |
| Decision cadence / worst freshness before computation | 60 simulated min |
| Controller peak working set observed via OS | 44.7 MB |
| Credits remaining | 400 |

The controller processes all available event chunks before the next advance. A new event can wait up to one simulated hour for the next decision, then one tick's computation and any governance delay. This is a cadence bound, not a measured percentile of individual decision times. Simulator history generation and the two-day bootstrap are excluded from the live throughput denominator. The controller bootstraps a bounded amount of history to avoid retaining every historical lead.

At 10×, one process doing HTTP NDJSON parsing and serial SQLite checkpoint serialization becomes the first throughput limit. Partition by workflow with a lease, transactional offset plus aggregate update, and a bounded worker queue. A real-time dialler would also require an alert on event age because its clock does not wait for decisions.

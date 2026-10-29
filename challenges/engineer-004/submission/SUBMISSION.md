fixture checksum [Observed]: 1aeb24b415009e89fcf8acb5a178410faf216dc17b16920d9849ecc8bbb24235
file: fixtures/event_sample.jsonl

```bash
shasum -a 256 fixtures/event_sample.jsonl
python3 submission/detect_anomalies.py
```

## Written answer

this answers brief version 2026-07.

what i care about

two seniors. aws. budget ceiling from the brief. cannot break the sdk. lots of tenants. they want live dashboards. today it is slow. fixture work is in fixtures/event_sample.jsonl and submission/detect_anomalies.py.

i care about four things.
1. do not lose events when traffic spikes.
2. a hot path fast enough for live dashboards and triggers.
3. migrate without breaking the customer sdk or integrations.
4. deletion and pii are in the design, not a later ticket.

for mvp i skip multi region active active and complex event processing. warehouse can be minutes late [Assumed]. live dashboards cannot.

architecture

```text
[existing JS SDK] --same url/payload--> [Collector ALB + ECS/Fargate]
                                              |
                                       ack after enqueue
                                              v
                                    [Kinesis Data Streams] -----> [S3 raw]
                                              |
                                              v
                                    [normalize + dedupe]
                                              |
              +-------------------------------+------------------------+
              v                               v                        v
        [DynamoDB]                      [Redis/MemoryDB]         [S3 curated / Firehose]
     identity + idempotency            hot counters +            warehouse export
     privacy tombstones                trigger flags             (Snowflake / BigQuery)
                                              |
                                              v
                                   [dashboard / personalization api]
                                              |
                                    [privacy worker fan-out delete]
```

why this.

collector in front of the same endpoint the sdk already hits. no sdk change. a new beacon url in mvp is still an sdk break.

kinesis as the buffer. managed. fine for two seniors. retention helps replay. i am not self hosting kafka for mvp.

dynamodb for identity and idempotency. keyed by tenant. postgres is fine for app data. it is a bad queue.

redis/memorydb for the hot path. dashboards and triggers read here. not snowflake.

s3 + async export for warehouses. minutes is fine there.

normalize after accept. fixture already shows alternate field names with no sdk bump [Observed] (detect_anomalies.py on event_sample.jsonl).

event shape after ingest (raw bytes kept):
event_id, tenant_id, anonymous_id, user_id, type, ts, received_at, properties, ingest_version

identity: on identify, store tenant_id + anonymous_id -> user_id. later events look that up. delete covers both keys and pre identify history. from fixture [Observed] in event_sample.jsonl: anon-9f2 -> u-5511, anon-c81 -> u-2209, anon-3d0 -> u-7304.

scale, reliability, migration

math [Estimated]: about 578 events/sec at brief daily volume. about 5.8k/sec at 10x. if events are about 1kb [Assumed] that is about 5.8 MB/sec at spike. size for the spike. raw about 50 GB/day at 1kb [Assumed]. fixture check uses detect_anomalies.py.

delivery: at least once into kinesis. sinks dedupe on (tenant_id, event_id). dashboards become effectively once. i do not claim perfect exactly once into every warehouse in mvp.

under pressure [Assumed]:
1. delay warehouse export
2. delay heavy segment work
3. coarsen rollups
4. return 429 so the sdk retries

do not quietly drop events like today.

migration:
1. shadow same sdk traffic into the new path. customers still read old.
2. compare about a week [Assumed]. counts by tenant/type/hour plus sample event_id overlap.
3. cut over by tenant groups.
4. rollback is a flag flip to old reads.

accuracy means a testable number. example [Assumed]: count diff under 0.1 percent for a week and canaries come back fully.

mvp in three months [Observed]: compatible collector, kinesis, raw s3, dedupe, normalizer, identity map, redis hot counters, basic dashboard api, privacy fan out v1, one warehouse path, parallel run checks.

mvp skips: multi region, full segment builder, every warehouse saas, ml scores, per tenant clusters.

budget [Estimated]: most of the monthly ceiling goes to stream, hot store, compute headroom, s3, egress. if money gets tight, cut retention and export frequency before durable ingest.

what is in the fixture

ran submission/detect_anomalies.py on fixtures/event_sample.jsonl. pipeline handling [Observed]:

```text
class                     event ids                    handling
duplicate delivery        evt-0002 (twice)             dedupe on (tenant_id, event_id)
client ts after received  evt-0005, evt-0006           use received_at for order; flag skew
future client ts          evt-0016 (year 2027)         quarantine or clamp bad ts; keep raw
schema drift              evt-0009                     normalize aliases; fill received_at
null tenant               evt-0011                     reject or quarantine; never cross tenant
bot/scanner burst         evt-0012..evt-0015           flag as bot; keep out of personalization
pii in properties         evt-0007                     redact before curated or export
privacy deletion          evt-0017                     control plane worker; fan out delete
client claimed aggregate  evt-0019 count_today         ignore; recompute on server
malformed json            evt-0020                     dlq + metric; do not crash consumer
identity stitch           anon-9f2 -> u-5511 etc       stitch graph; deletes cover anon history
```

stuff i will not take as truth

1. count_today on evt-0019 [Observed] in event_sample.jsonl. client talking. not proof.
2. four bot hits are not the bot rate of full daily volume [Assumed].
3. tiny sample does not prove brief loss or latency numbers [Assumed]. fixture is for classes. see detect_anomalies.py.
4. pii looks synthetic. still design pii controls. do not claim a real breach from the sample.
5. null referrer on a normal page view is usually fine.

tradeoffs

i pick durability and something two seniors can run. warehouse slower. mvp segments are simple counters. risks: hot tenant melting redis, under provision before a spike, deletion missing a sink. with more time: harder load tests, second region later, tighter warehouse export.

assumptions [Assumed]: sdk already hits an http endpoint we can front. average event about 1kb. customers accept warehouse lag in minutes if live dashboard is live.

## Operating artifact

1. submission/detect_anomalies.py
2. this writeup
3. architecture diagram above

```bash
cd challenges/engineer-004
python3 submission/detect_anomalies.py
```

## Evidence log

| claim | tier | proof |
|------|------|-------|
| fixture checksum and anomaly classes with event ids | Tier 3 | fixtures/event_sample.jsonl + detect_anomalies.py |
| architecture and migration plan | Tier 2 | this writeup + diagram in SUBMISSION.md |
| daily volume, latency target, budget ceiling, peak loss | Tier 1 | brief text, labeled below |
| spike eps and storage sizing | Tier 0 | Estimated / Assumed math |
| cost fits monthly ceiling | Tier 0/1 | Estimated sketch only |

```bash
python3 submission/detect_anomalies.py
```

## Number source labels

[Observed] [Estimated] [Benchmarked] [Assumed]

- [Observed] from brief: about 50M events/day, 15 to 30 min latency today, under 5 second target, about 3 percent peak loss, 50k usd/month ceiling, 2 seniors, 3 month mvp / 6 month full, 500+ tenants, aws only, no sdk break
- [Estimated] about 578 events/sec average, about 5.8k events/sec at 10x
- [Assumed] about 1kb per event, about 5.8 MB/sec at spike, about 50 GB/day raw
- [Assumed] parallel run pass around 0.1 percent count diff for a week
- [Observed] fixture sha256 and event ids from fixtures/event_sample.jsonl via detect_anomalies.py

```bash
python3 submission/detect_anomalies.py
```

## AI usage disclosure

tools: cursor for repo pull, fixture parsing, writeup help.

what i decided: architecture, degrade order, migration steps, what not to trust in the fixture.

what i checked myself [Observed]: reran sha256, ran detect_anomalies.py, read every line in event_sample.jsonl including broken json on evt-0020.

limits: cost is rough. new latency is a design target, not a load test result.

## Failure handling / what breaks it

from fixtures/event_sample.jsonl and detect_anomalies.py [Observed]:
- no headroom at spike and no 429 path. lag, then drops.
- trust client ts. wrong windows. evt-0005, evt-0006, evt-0016.
- no dedupe. double counts. evt-0002.
- null tenant into a default bucket. cross tenant mess. evt-0011.
- delete only sql, miss s3/redis/warehouse. gdpr fail. evt-0017.
- crash consumer on one bad json line. stall. evt-0020.
- trust count_today. spoofed segments. evt-0019.

what stays human
- cutover and rollback decisions
- privacy delete exceptions and legal holds
- when a burst might be a customer load test, not a bot
- budget calls when spike cost threatens the monthly ceiling

automation can flag. people still decide cutover and deletes.

## Artifact access

no login needed.

```text
challenges/engineer-004/submission/SUBMISSION.md
challenges/engineer-004/submission/detect_anomalies.py
challenges/engineer-004/fixtures/event_sample.jsonl
```

github: https://github.com/kibrukuture/beat-claude-engineer-004

```bash
git clone https://github.com/kibrukuture/beat-claude-engineer-004
cd beat-claude-engineer-004/challenges/engineer-004
python3 submission/detect_anomalies.py
```

expected [Observed]: checksum 1aeb24b415009e89fcf8acb5a178410faf216dc17b16920d9849ecc8bbb24235 and the finding classes above.

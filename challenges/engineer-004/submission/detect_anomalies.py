#!/usr/bin/env python3
"""Scan fixtures/event_sample.jsonl and print odd rows.

  python3 submission/detect_anomalies.py
  python3 submission/detect_anomalies.py --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "fixtures" / "event_sample.jsonl"

PII_KEYS = re.compile(r"(email|phone|ssn|password|secret|token)", re.I)
BOT_HINT = re.compile(r"bot|crawler|scanner|spider", re.I)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except ValueError:
        return None


def load_rows(path: Path) -> tuple[list[dict], list[dict]]:
    ok: list[dict] = []
    bad: list[dict] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
            obj["_line"] = line_no
            ok.append(obj)
        except json.JSONDecodeError as exc:
            m = re.search(r'"event_id"\s*:\s*"([^"]+)"', raw)
            bad.append(
                {
                    "line": line_no,
                    "event_id": m.group(1) if m else None,
                    "error": str(exc),
                    "raw_preview": raw[:160],
                }
            )
    return ok, bad


def detect(path: Path) -> dict:
    rows, malformed = load_rows(path)
    findings: list[dict] = []

    by_id: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_id[str(row.get("event_id"))].append(row)

    for event_id, copies in by_id.items():
        if len(copies) > 1:
            findings.append(
                {
                    "class": "duplicate_event_id",
                    "event_ids": [event_id],
                    "lines": [c["_line"] for c in copies],
                    "detail": "same event_id more than once",
                }
            )

    for row in rows:
        eid = row.get("event_id")
        ts = parse_ts(row.get("ts") or row.get("timestamp"))
        received = parse_ts(row.get("received_at"))
        if ts and received and ts > received:
            delta = (ts - received).total_seconds()
            findings.append(
                {
                    "class": "client_ts_after_received_at",
                    "event_ids": [eid],
                    "lines": [row["_line"]],
                    "detail": f"client ts is {delta:.1f}s after received_at",
                }
            )
        if ts and received and ts.year > received.year:
            findings.append(
                {
                    "class": "future_client_timestamp",
                    "event_ids": [eid],
                    "lines": [row["_line"]],
                    "detail": f"client ts year {ts.year} vs received year {received.year}",
                }
            )

    for row in rows:
        props = row.get("properties") or {}
        drifted = False
        reasons = []
        if row.get("type") == "pageview":
            drifted = True
            reasons.append("type=pageview alias")
        if "timestamp" in row and "ts" not in row:
            drifted = True
            reasons.append("timestamp instead of ts")
        if "received_at" not in row:
            drifted = True
            reasons.append("missing received_at")
        if "page_path" in props or "ref" in props:
            drifted = True
            reasons.append("legacy property keys page_path/ref")
        if drifted:
            findings.append(
                {
                    "class": "schema_drift",
                    "event_ids": [row.get("event_id")],
                    "lines": [row["_line"]],
                    "detail": ", ".join(reasons),
                }
            )

    for row in rows:
        if row.get("tenant_id") in (None, ""):
            findings.append(
                {
                    "class": "null_tenant",
                    "event_ids": [row.get("event_id")],
                    "lines": [row["_line"]],
                    "detail": "tenant_id is null/empty",
                }
            )

    bot_ids = []
    bot_lines = []
    for row in rows:
        props = row.get("properties") or {}
        ref = str(props.get("referrer") or props.get("ref") or "")
        if BOT_HINT.search(ref):
            bot_ids.append(row.get("event_id"))
            bot_lines.append(row["_line"])
    if bot_ids:
        findings.append(
            {
                "class": "bot_or_scanner_burst",
                "event_ids": bot_ids,
                "lines": bot_lines,
                "detail": "referrer matched bot/scanner pattern",
            }
        )

    for row in rows:
        props = row.get("properties") or {}
        hits = [k for k in props if PII_KEYS.search(k)]
        if hits:
            findings.append(
                {
                    "class": "pii_in_properties",
                    "event_ids": [row.get("event_id")],
                    "lines": [row["_line"]],
                    "detail": f"pii-pattern keys: {', '.join(hits)}",
                }
            )

    for row in rows:
        if row.get("type") == "privacy_request":
            findings.append(
                {
                    "class": "privacy_deletion_request",
                    "event_ids": [row.get("event_id")],
                    "lines": [row["_line"]],
                    "detail": "type is privacy_request",
                }
            )

    for row in rows:
        props = row.get("properties") or {}
        if "count_today" in props:
            findings.append(
                {
                    "class": "client_claimed_aggregate",
                    "event_ids": [row.get("event_id")],
                    "lines": [row["_line"]],
                    "detail": "properties include count_today",
                }
            )

    for item in malformed:
        findings.append(
            {
                "class": "malformed_json",
                "event_ids": [item.get("event_id")],
                "lines": [item["line"]],
                "detail": item["error"],
            }
        )

    by_anon: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        anon = row.get("anonymous_id")
        uid = row.get("user_id")
        if anon and uid:
            by_anon[str(anon)].add(str(uid))
    stitch = {anon: sorted(uids) for anon, uids in by_anon.items() if uids}

    return {
        "fixture": str(path),
        "sha256": sha256_file(path),
        "lines_total": sum(1 for _ in path.read_text(encoding="utf-8").splitlines() if _.strip()),
        "parseable": len(rows),
        "malformed_count": len(malformed),
        "unique_event_ids_parseable": len({r.get("event_id") for r in rows}),
        "tenants": sorted({r.get("tenant_id") for r in rows}, key=lambda x: (x is None, str(x))),
        "identity_stitch_examples": stitch,
        "findings": findings,
    }


def print_human(report: dict) -> None:
    print(f"fixture: {report['fixture']}")
    print(f"sha256:  {report['sha256']}")
    print(
        f"rows: {report['lines_total']} lines, "
        f"{report['parseable']} parseable, "
        f"{report['malformed_count']} malformed"
    )
    print(f"tenants: {report['tenants']}")
    print()
    print("findings:")
    if not report["findings"]:
        print("  (none)")
    for f in report["findings"]:
        ids = ", ".join(str(x) for x in f["event_ids"] if x is not None)
        print(f"- [{f['class']}] {ids} (lines {f['lines']}): {f['detail']}")
    print()
    print("identity stitch examples (anon -> user_ids):")
    for anon, uids in sorted(report["identity_stitch_examples"].items()):
        print(f"- {anon} -> {', '.join(uids)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.fixture.exists():
        print(f"missing fixture: {args.fixture}", file=sys.stderr)
        return 1
    report = detect(args.fixture)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

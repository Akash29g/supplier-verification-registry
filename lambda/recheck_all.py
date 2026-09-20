"""
Nightly EventBridge job (see RecheckAllFunction in infra/template.yaml).

Re-verifies live suppliers against the live GST lookup, re-runs the risk rules
(including the cross-supplier ring rules), writes a VerificationLog row for each
re-check so it shows up in History, and revokes any public badge whose supplier
is no longer VERIFIED.

Safe by design:
  * crafted test cases are skipped (they aren't real registered entities);
  * if the live source fails, the last good data is kept - a flaky API never
    turns a healthy supplier into a false alarm;
  * bounded work per run (oldest-checked first), so repeated runs rotate
    through everyone instead of timing out.
Invoke it manually from the Lambda console with any test event ({}).
"""

import json
import uuid

from agents import risk_agent
from agents.trace import step, now
from agents.verification_agent import verify_gstin

from . import store

MAX_PER_RUN = 20
STOP_WHEN_MS_LEFT = 25_000


def _live_rows():
    table, rows, kwargs = store.suppliers_table(), [], {}
    while True:
        resp = table.scan(**kwargs)
        rows.extend(r for r in resp.get("Items", []) if r.get("data_origin") == "live_api")
        if "LastEvaluatedKey" not in resp:
            return rows
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _recheck_one(row):
    """Re-verify one supplier and save its own findings. Returns a work item, or None if skipped."""
    trace, t = [], now()
    result = verify_gstin(row["gstin"], trace=trace).to_dict()
    if result["status"] == "failed":
        return None  # live source unavailable: keep last good data

    lookup = (result.get("data") or {}).get("gstin_lookup") or {}
    own = risk_agent.own_findings(result, store.declared_from_row(row), trace=trace)
    ts, log_id = store.now_ts(), str(uuid.uuid4())

    updated = dict(row)
    updated.update({
        "status": result["status"],
        "source": result["source"] or "none",
        "registration_status": lookup.get("registration_status", "") or "",
        "own_findings_json": json.dumps(own),
        "last_verified_at": ts,
        "last_log_id": log_id,
    })
    legal_name = lookup.get("legal_name")
    if legal_name and legal_name != "UNKNOWN":
        updated["legal_name"] = legal_name
    store.suppliers_table().put_item(Item=updated)

    return {"row": updated, "result": result, "trace": trace, "log_id": log_id, "ts": ts,
            "previous": row.get("verdict", ""), "started": t}


def handler(event, context):
    rows = sorted(_live_rows(), key=lambda r: int(r.get("last_verified_at", 0)))[:MAX_PER_RUN]
    summary = {"candidates": len(rows), "checked": 0, "changed": 0, "skipped": 0, "errors": 0}

    by_owner = {}
    for row in rows:
        if context.get_remaining_time_in_millis() < STOP_WHEN_MS_LEFT:
            break
        try:
            item = _recheck_one(row)
        except Exception as e:  # one bad supplier must not stop the run
            print(f"recheck failed for {row.get('gstin')}: {type(e).__name__}: {e}")
            summary["errors"] += 1
            continue
        if item is None:
            summary["skipped"] += 1
            continue
        by_owner.setdefault(row["owner_user_id"], []).append(item)

    for owner, items in by_owner.items():
        # Cross-supplier rules need every row in place first, then one refresh per owner.
        assessments = store.refresh_assessments(owner)
        rechecked_at = {}
        for item in items:
            gstin, row = item["row"]["gstin"], item["row"]
            a = assessments.get(gstin)
            if not a:
                continue
            changed = item["previous"] != a["verdict"]
            step(item["trace"], "scheduler",
                 f"Nightly re-check: verdict {item['previous'] or 'unscored'} -> {a['verdict']}" if changed
                 else f"Nightly re-check: verdict unchanged ({a['verdict']})",
                 ok=(not changed) or a["verdict"] == "VERIFIED", started=item["started"])
            store.log_table().put_item(Item={
                "owner_user_id": owner, "log_id": item["log_id"], "gstin": gstin,
                "name": row.get("legal_name") or row.get("name", ""),
                "status": row["status"], "verdict": a["verdict"], "trust_score": a["trust_score"],
                "findings_json": json.dumps(a["findings"]), "trace_json": json.dumps(item["trace"]),
                "data_origin": "live_api", "source": row["source"], "error": "",
                "timestamp": item["ts"], "raw_result": json.dumps(item["result"]),
                "trigger": "scheduled_recheck",
            })
            store.bump_verification_counter()
            rechecked_at[gstin] = item["ts"]
            summary["checked"] += 1
            summary["changed"] += int(changed)

        # Refresh or revoke badges for everything the owner has (a new ring can flip a
        # supplier we didn't re-check this run).
        for gstin, a in assessments.items():
            store.sync_badge(owner, gstin, a["verdict"], a["trust_score"], rechecked_at.get(gstin))

    print(json.dumps(summary))
    return {"statusCode": 200, "body": json.dumps(summary)}

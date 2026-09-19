"""
POST /demo/seed - loads the project's two seed sets into the CALLER's account:

  data/seed_legit.json  5 real companies, pulled live earlier and cached
                        (data_origin = live_api)
  data/seed_fraud.json  hand-crafted fraud cases (data_origin = crafted_test_case)

Crafted GSTINs are structurally valid but not real registered entities, so
they are written directly instead of going through the live GST lookup. They
still run through the SAME risk rules as a live check - the shared-bank-account
ring is detected by risk_agent.ring_findings(), not hardcoded here.

Idempotent: suppliers are upserts on (owner, gstin) and log ids are
deterministic ("seed-<gstin>"), so pressing the button twice changes nothing.
"""

import json
import os
from datetime import date, timedelta

from botocore.exceptions import ClientError

from agents import risk_agent

from . import common, store

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# The seed file stores an absolute registration date, which would silently age
# out of the "very recently registered" rule as real time passes. Pin the
# crafted shell company to a fixed age relative to whenever it is seeded.
CRAFTED_REGISTERED_DAYS_AGO = {"fraud_002_shell_company": 35}


def _load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def _crafted_rows(owner, today, ts):
    rows = []
    for case in _load("seed_fraud.json")["cases"]:
        if not case.get("supplier_id"):
            continue  # fraud_001 is a bare malformed GSTIN - demo it through /verify instead
        declared = {k: case[k] for k in ("name", "bank_account", "address", "claimed_business",
                                        "paid_up_capital_inr") if case.get(k) not in (None, "")}
        days_ago = CRAFTED_REGISTERED_DAYS_AGO.get(case["case_id"])
        if days_ago is not None:
            declared["registered_date"] = (today - timedelta(days=days_ago)).isoformat()
        elif case.get("registered_date"):
            declared["registered_date"] = case["registered_date"]

        own = risk_agent.own_findings(None, declared, today=today)
        row = {
            "owner_user_id": owner, "gstin": case["gstin"],
            "legal_name": case["name"], "status": "crafted",
            "source": "crafted_test_case", "data_origin": "crafted_test_case",
            "registration_status": "", "own_findings_json": json.dumps(own),
            "last_verified_at": ts, "last_log_id": f"seed-{case['gstin']}",
        }
        row.update(declared)
        rows.append((row, "crafted_test_case", "Loaded crafted test case from data/seed_fraud.json "
                                               "(not a real registered entity)."))
    return rows


def _legit_rows(owner, today, ts):
    rows = []
    for company in _load("seed_legit.json")["companies"]:
        result = {"gstin": company["gstin"], "status": company.get("status", "degraded"),
                  "source": "gstincheck.co.in", "data": company.get("data", {}), "error": None}
        lookup = (result["data"] or {}).get("gstin_lookup") or {}
        own = risk_agent.own_findings(result, {}, today=today)
        row = {
            "owner_user_id": owner, "gstin": company["gstin"],
            "legal_name": lookup.get("legal_name", ""), "status": result["status"],
            "source": "gstincheck.co.in (cached)", "data_origin": "live_api",
            "registration_status": lookup.get("registration_status", ""),
            "own_findings_json": json.dumps(own),
            "last_verified_at": ts, "last_log_id": f"seed-{company['gstin']}",
        }
        reg_date = risk_agent.parse_date(((lookup.get("raw") or {}).get("rgdt")))
        if reg_date:
            row["registered_date"] = reg_date.isoformat()
        rows.append((row, "live_api", "Loaded from cached live GSTIN lookup in data/seed_legit.json."))
    return rows


def handler(event, context):
    owner = common.get_user_id(event)
    if not owner:
        return common.fail(401, "missing_or_invalid_token")

    try:
        today, ts = date.today(), store.now_ts()
        seeded = _legit_rows(owner, today, ts) + _crafted_rows(owner, today, ts)

        for row, _, _ in seeded:
            store.suppliers_table().put_item(Item=row)

        # Now every node exists, so ring rules can see each other.
        assessments = store.refresh_assessments(owner)

        for row, origin, note in seeded:
            a = assessments[row["gstin"]]
            store.log_table().put_item(Item={
                "owner_user_id": owner, "log_id": row["last_log_id"], "gstin": row["gstin"],
                "name": row["legal_name"], "status": row["status"],
                "verdict": a["verdict"], "trust_score": a["trust_score"],
                "findings_json": json.dumps(a["findings"]),
                "trace_json": json.dumps([{"agent": "seed", "message": note, "ok": True, "ms": None}]),
                "data_origin": origin, "source": row["source"], "error": "",
                "timestamp": ts, "raw_result": json.dumps({"gstin": row["gstin"], "seeded": True}),
            })

        by_verdict = {}
        for a in assessments.values():
            by_verdict[a["verdict"]] = by_verdict.get(a["verdict"], 0) + 1
        return common.ok({"seeded": len(seeded)}, {"by_verdict": by_verdict})

    except ClientError as e:
        return common.fail(500, "dynamodb_error:" + e.response["Error"]["Message"])

import json
import re
import uuid

from botocore.exceptions import ClientError

from agents.verification_agent import verify_gstin
from agents import risk_agent
from agents.trace import step, now
from agents import summary_agent

from . import common, store

MAX_FIELD_LEN = 200


def _clean(value, max_len=MAX_FIELD_LEN):
    return str(value).strip()[:max_len] if value not in (None, "") else ""


def _parse_declared(body):
    """Optional supplier details the buyer typed in. Anything omitted stays empty."""
    declared = {}
    for key in ("name", "address"):
        v = _clean(body.get(key))
        if v:
            declared[key] = v
    bank = re.sub(r"\D", "", str(body.get("bank_account") or ""))[:20]
    if bank:
        declared["bank_account"] = bank
    return declared


def handler(event, context):
    user_id = common.get_user_id(event)
    if not user_id:
        return common.response(401, {"error": "missing_or_invalid_token"})

    try:
        body = json.loads(event.get("body") or "{}")
        gstin = (body.get("gstin") or "").strip().upper()
        if not gstin:
            return common.response(400, {"error": "gstin_required"})

        trace = []

        # ---- agent 1: verification (live government-sourced data) ----
        result = verify_gstin(gstin, trace=trace)
        result_dict = result.to_dict()
        format_invalid = (result_dict.get("error") or "").startswith("invalid_gstin:")

        # Declared details: what the buyer typed now, falling back to what we
        # already know about this supplier so re-verifying never wipes them.
        existing = None if format_invalid else store.get_supplier(user_id, gstin)
        declared = store.declared_from_row(existing) if existing else {}
        declared.update(_parse_declared(body))

        # ---- agent 2: risk (single-supplier rules) ----
        own = risk_agent.own_findings(result_dict, declared, trace=trace)

        now_ts = store.now_ts()
        log_id = str(uuid.uuid4())
        lookup = (result_dict.get("data") or {}).get("gstin_lookup") or {}
        legal_name = lookup.get("legal_name") if lookup.get("legal_name") != "UNKNOWN" else ""

        if format_invalid:
            # Malformed GSTINs are logged (so /history shows the REJECT) but are
            # not saved as suppliers - typos shouldn't clutter the dashboard.
            assessment = risk_agent.build_assessment(own)
        else:
            row = {
                "owner_user_id": user_id,
                "gstin": gstin,
                "legal_name": legal_name or "",
                "status": result_dict["status"],
                "source": result_dict["source"] or "none",
                "data_origin": "live_api",
                "registration_status": lookup.get("registration_status", "") or "",
                "own_findings_json": json.dumps(own),
                "last_verified_at": now_ts,
                "last_log_id": log_id,
            }
            row.update(declared)
            raw_rgdt = (lookup.get("raw") or {}).get("rgdt")
            reg_date = risk_agent.parse_date(raw_rgdt)
            if reg_date:
                row["registered_date"] = reg_date.isoformat()
            store.suppliers_table().put_item(Item=row)

            # ---- agent 2b: risk (cross-supplier rules) over ALL of this owner's
            # suppliers. Also updates OTHER suppliers that just became part of a ring.
            t = now()
            assessments = store.refresh_assessments(user_id)
            assessment = assessments[gstin]
            step(trace, "risk",
                 f"Verdict: {assessment['verdict']} (trust score {assessment['trust_score']}/100)",
                 ok=assessment["verdict"] == "VERIFIED", started=t)

        if format_invalid:
            step(trace, "risk",
                 f"Verdict: {assessment['verdict']} (trust score {assessment['trust_score']}/100)",
                 ok=False)

        name_for_summary = legal_name or declared.get("name", "") or gstin
        summary_text, summary_source = summary_agent.summarize(
        name_for_summary, assessment["verdict"], assessment["trust_score"], assessment["findings"], trace=trace)    

        store.log_table().put_item(Item={
            "owner_user_id": user_id,
            "log_id": log_id,
            "gstin": gstin,
            "name": legal_name or declared.get("name", ""),
            "status": result_dict["status"],
            "verdict": assessment["verdict"],
            "trust_score": assessment["trust_score"],
            "findings_json": json.dumps(assessment["findings"]),
            "trace_json": json.dumps(trace),
            "data_origin": "live_api",
            "source": result_dict["source"] or "none",
            "error": result_dict.get("error") or "",
            "timestamp": now_ts,
            "raw_result": json.dumps(result_dict),
            "summary": summary_text, 
            "summary_source": summary_source,
        })

        store.bump_verification_counter()

        out = dict(result_dict)          # legacy flat shape kept: gstin/status/source/data/error
        out["log_id"] = log_id
        out["assessment"] = assessment
        out["trace"] = trace
        out["summary"] = summary_text; 
        out["summary_source"] = summary_source
        return common.response(200, out)

    except ClientError as e:
        return common.response(500, {"error": "dynamodb_write_failed", "detail": e.response["Error"]["Message"]})
    except Exception as e:
        return common.response(500, {"error": "unexpected_error", "detail": str(e)})

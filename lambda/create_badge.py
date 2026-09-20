"""
POST /badges  { "log_id": "<a verification the caller owns>" }

Opt-in: the owner publishes a public, read-only badge for one supplier. Only
VERIFIED suppliers can get one. The badge is a snapshot of five safe fields
(name, GSTIN, GST status, score, date) - never bank details, findings or the
owner's identity. The id is stable per (owner, supplier), so issuing it twice
refreshes the same link instead of creating a second one.
"""

import json

from botocore.exceptions import ClientError

from . import common, store


def handler(event, context):
    user_id = common.get_user_id(event)
    if not user_id:
        return common.fail(401, "missing_or_invalid_token")

    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        return common.fail(400, "invalid_json")
    log_id = (body.get("log_id") or "").strip()
    if not log_id:
        return common.fail(400, "log_id_required")

    # Both lookups are keyed by the caller's user id, so someone else's log_id
    # is simply "not found" - same 404-not-403 rule as /reports.
    log = store.log_table().get_item(Key={"owner_user_id": user_id, "log_id": log_id}).get("Item")
    if not log:
        return common.fail(404, "not_found")
    supplier = store.get_supplier(user_id, log["gstin"])
    if not supplier:
        return common.fail(404, "not_found")

    # Use the supplier's CURRENT verdict (ring rules may have changed it since this log).
    if supplier.get("verdict") != "VERIFIED":
        return common.fail(400, "badge_only_for_verified")

    badge_id = store.badge_id_for(user_id, supplier["gstin"])
    try:
        store.badges_table().put_item(Item={
            "badge_id": badge_id,
            "gstin": supplier["gstin"],
            "name": supplier.get("legal_name") or supplier.get("name") or supplier["gstin"],
            "registration_status": supplier.get("registration_status", ""),
            "trust_score": int(supplier["trust_score"]),
            "verified_at": int(supplier.get("last_verified_at") or log.get("timestamp") or store.now_ts()),
            "issued_at": store.now_ts(),
        })
    except ClientError as e:
        return common.fail(500, "dynamodb_error:" + e.response["Error"]["Message"])

    return common.ok({"badge_id": badge_id})

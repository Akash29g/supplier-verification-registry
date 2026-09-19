import json

from . import common, store


def handler(event, context):
    user_id = common.get_user_id(event)
    if not user_id:
        return common.fail(401, "missing_or_invalid_token")

    log_id = ((event.get("pathParameters") or {}).get("log_id") or "").strip()
    if not log_id:
        return common.fail(400, "log_id_required")

    # The key is (owner_user_id, log_id): someone else's log_id simply isn't
    # found for this user, so we answer 404 - never 403, which would confirm
    # that the record exists.
    row = store.log_table().get_item(Key={"owner_user_id": user_id, "log_id": log_id}).get("Item")
    if not row:
        return common.fail(404, "not_found")

    return common.ok({
        "log_id": row["log_id"],
        "gstin": row["gstin"],
        "name": row.get("name", ""),
        "status": row.get("status", ""),
        "verdict": row.get("verdict", ""),
        "trust_score": int(row["trust_score"]) if row.get("trust_score") is not None else None,
        "findings": json.loads(row.get("findings_json") or "[]"),
        "trace": json.loads(row.get("trace_json") or "[]"),
        "source": row.get("source", ""),
        "data_origin": row.get("data_origin", "live_api"),
        "error": row.get("error", ""),
        "timestamp": int(row.get("timestamp", 0)),
        "result": json.loads(row.get("raw_result") or "{}"),
    })

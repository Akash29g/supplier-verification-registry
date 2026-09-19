import json

from . import common, store

MAX_LIMIT = 200


def handler(event, context):
    user_id = common.get_user_id(event)
    if not user_id:
        return common.fail(401, "missing_or_invalid_token")

    params = event.get("queryStringParameters") or {}
    gstin_filter = (params.get("gstin") or "").strip().upper()
    try:
        limit = max(1, min(int(params.get("limit", 100)), MAX_LIMIT))
    except ValueError:
        return common.fail(400, "invalid_limit")

    rows = store.query_all(store.log_table(), user_id)
    if gstin_filter:
        rows = [r for r in rows if r["gstin"].startswith(gstin_filter)]
    rows.sort(key=lambda r: int(r.get("timestamp", 0)), reverse=True)

    items = [{
        "log_id": r["log_id"],
        "gstin": r["gstin"],
        "name": r.get("name", ""),
        "status": r.get("status", ""),
        "verdict": r.get("verdict", ""),
        "trust_score": int(r["trust_score"]) if r.get("trust_score") is not None else None,
        "source": r.get("source", ""),
        "data_origin": r.get("data_origin", "live_api"),
        "error": r.get("error", ""),
        "timestamp": int(r.get("timestamp", 0)),
    } for r in rows[:limit]]

    return common.ok(items, {"total": len(rows), "returned": len(items)})

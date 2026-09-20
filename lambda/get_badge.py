"""GET /badges/{badge_id} - PUBLIC (no auth): the read-only data behind /#/badge/<id>."""

from . import common, store


def handler(event, context):
    badge_id = ((event.get("pathParameters") or {}).get("badge_id") or "").strip()
    if not badge_id:
        return common.fail(400, "badge_id_required")

    item = store.badges_table().get_item(Key={"badge_id": badge_id}).get("Item")
    if not item:
        return common.fail(404, "not_found")

    return common.ok({
        "badge_id": item["badge_id"],
        "gstin": item["gstin"],
        "name": item.get("name", ""),
        "registration_status": item.get("registration_status", ""),
        "trust_score": int(item["trust_score"]),
        "verified_at": int(item.get("verified_at", 0)),
    })

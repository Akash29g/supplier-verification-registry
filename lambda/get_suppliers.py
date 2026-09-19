from . import common, store


def handler(event, context):
    user_id = common.get_user_id(event)
    if not user_id:
        return common.fail(401, "missing_or_invalid_token")

    rows = store.query_all(store.suppliers_table(), user_id)
    items = sorted((store.supplier_view(r) for r in rows),
                   key=lambda s: s["last_verified_at"], reverse=True)

    by_verdict = {}
    for s in items:
        by_verdict[s["verdict"] or "UNSCORED"] = by_verdict.get(s["verdict"] or "UNSCORED", 0) + 1

    return common.ok(items, {"total": len(items), "by_verdict": by_verdict})

from agents import risk_agent
from . import common, store


def handler(event, context):
    user_id = common.get_user_id(event)
    if not user_id:
        return common.fail(401, "missing_or_invalid_token")

    rows = store.query_all(store.suppliers_table(), user_id)

    nodes = [{
        "id": r["gstin"],
        "name": r.get("legal_name") or r.get("name") or r["gstin"],
        "verdict": r.get("verdict", ""),
        "trust_score": int(r["trust_score"]) if r.get("trust_score") is not None else None,
        "data_origin": r.get("data_origin", "live_api"),
    } for r in rows]

    edges = []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            reasons = []
            ab, bb = risk_agent.norm_bank(a.get("bank_account")), risk_agent.norm_bank(b.get("bank_account"))
            if ab and ab == bb:
                reasons.append("shared_bank_account")
            aa, ba = risk_agent.norm_address(a.get("address")), risk_agent.norm_address(b.get("address"))
            if aa and aa == ba:
                reasons.append("shared_address")
            if reasons:
                edges.append({"source": a["gstin"], "target": b["gstin"], "reasons": reasons})

    return common.ok({"nodes": nodes, "edges": edges}, {"node_count": len(nodes), "edge_count": len(edges)})
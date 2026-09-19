from . import common, store


def handler(event, context):
    """Public (no auth): the real, shared verification counter for the landing page."""
    item = store.stats_table().get_item(Key={"stat_id": "global"}).get("Item") or {}
    return common.ok({"total_checks": int(item.get("total_checks", 0))})

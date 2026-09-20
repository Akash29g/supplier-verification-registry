"""
DynamoDB helpers shared by verify_supplier and demo_seed.

Key idea: each Suppliers row stores the supplier's OWN findings (rules that
need only that supplier) plus the declared fields (bank_account, address...).
Cross-supplier findings (shared bank account / address) are NOT stored - they
are recomputed from all of the owner's rows by refresh_assessments(). That is
why adding one new supplier can flip an older supplier to RISK: the ring only
exists once both nodes are in the table.
"""

import hashlib
import json
import os
import time

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from agents import risk_agent

dynamodb = boto3.resource("dynamodb")

DECLARED_FIELDS = (
    "name", "bank_account", "address",
    "registered_date", "paid_up_capital_inr", "claimed_business",
)


def suppliers_table():
    return dynamodb.Table(os.environ["SUPPLIERS_TABLE"])


def log_table():
    return dynamodb.Table(os.environ["VERIFICATION_LOG_TABLE"])


def stats_table():
    return dynamodb.Table(os.environ["STATS_TABLE"])


def badges_table():
    return dynamodb.Table(os.environ["BADGES_TABLE"])


def badge_id_for(owner_user_id, gstin):
    """Stable public id per (owner, supplier): re-issuing a badge updates the same link."""
    return hashlib.sha256(f"{owner_user_id}:{gstin}".encode()).hexdigest()[:12]


def sync_badge(owner_user_id, gstin, verdict, trust_score, checked_at=None):
    """
    Keep a published badge honest. While the supplier is still VERIFIED the badge
    is refreshed; the moment it isn't, the badge is deleted (public link 404s).
    A supplier that never had a badge is a no-op either way.
    """
    badge_id = badge_id_for(owner_user_id, gstin)
    if verdict != "VERIFIED":
        badges_table().delete_item(Key={"badge_id": badge_id})
        return
    expr, vals = "SET trust_score = :s", {":s": int(trust_score)}
    if checked_at:
        expr += ", verified_at = :t"
        vals[":t"] = int(checked_at)
    try:
        badges_table().update_item(
            Key={"badge_id": badge_id},
            UpdateExpression=expr,
            ConditionExpression="attribute_exists(badge_id)",
            ExpressionAttributeValues=vals,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def query_all(table, owner_user_id):
    """Every row for one owner (partition key), following pagination."""
    items, kwargs = [], {"KeyConditionExpression": Key("owner_user_id").eq(owner_user_id)}
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def get_supplier(owner_user_id, gstin):
    resp = suppliers_table().get_item(Key={"owner_user_id": owner_user_id, "gstin": gstin})
    return resp.get("Item")


def declared_from_row(row):
    declared = {k: row.get(k) for k in DECLARED_FIELDS if row.get(k) not in (None, "")}
    if "paid_up_capital_inr" in declared:
        declared["paid_up_capital_inr"] = int(declared["paid_up_capital_inr"])
    return declared


def refresh_assessments(owner_user_id, rows=None):
    """
    Recompute verdict/score for every supplier the owner has, using each row's
    stored own_findings plus fresh ring findings against all the other rows.
    Writes back only rows whose result changed. Returns {gstin: assessment}.
    """
    rows = rows if rows is not None else query_all(suppliers_table(), owner_user_id)
    result = {}
    for row in rows:
        if "own_findings_json" not in row:
            continue  # legacy row from before risk scoring existed: stays "Not scored" until re-verified
        own = json.loads(row.get("own_findings_json") or "[]")
        peers = [r for r in rows if r["gstin"] != row["gstin"]]
        ring = risk_agent.ring_findings(row["gstin"], declared_from_row(row), peers)
        assessment = risk_agent.build_assessment(own + ring)
        result[row["gstin"]] = assessment

        findings_json = json.dumps(assessment["findings"])
        if (row.get("verdict") != assessment["verdict"]
                or int(row.get("trust_score", -1)) != assessment["trust_score"]
                or row.get("findings_json") != findings_json):
            suppliers_table().update_item(
                Key={"owner_user_id": owner_user_id, "gstin": row["gstin"]},
                UpdateExpression="SET verdict = :v, trust_score = :s, findings_json = :f",
                ExpressionAttributeValues={
                    ":v": assessment["verdict"],
                    ":s": assessment["trust_score"],
                    ":f": findings_json,
                },
            )
    return result


def bump_verification_counter():
    """Atomic, shared, real counter behind the landing page number."""
    stats_table().update_item(
        Key={"stat_id": "global"},
        UpdateExpression="ADD total_checks :one",
        ExpressionAttributeValues={":one": 1},
    )


def supplier_view(row):
    """Row -> JSON-safe dict for the API (parses stored JSON strings)."""
    return {
        "gstin": row["gstin"],
        "name": row.get("legal_name") or row.get("name") or "",
        "declared_name": row.get("name", ""),
        "legal_name": row.get("legal_name", ""),
        "bank_account_masked": risk_agent.mask_bank(row["bank_account"]) if row.get("bank_account") else "",
        "address": row.get("address", ""),
        "status": row.get("status", ""),
        "source": row.get("source", ""),
        "data_origin": row.get("data_origin", "live_api"),
        "verdict": row.get("verdict", ""),
        "trust_score": int(row["trust_score"]) if row.get("trust_score") is not None else None,
        "findings": json.loads(row.get("findings_json") or "[]"),
        "registration_status": row.get("registration_status", ""),
        "last_verified_at": int(row.get("last_verified_at", 0)),
        "last_log_id": row.get("last_log_id", ""),
    }


def now_ts():
    return int(time.time())

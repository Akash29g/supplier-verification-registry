"""
Handler-level tests against moto (in-memory DynamoDB). No network, no AWS account.
Covers: two users are isolated, ring detection flips verdicts across suppliers,
seeding is idempotent, /reports never leaks across users, counter is atomic.
"""
import importlib
import json
import os

import boto3
import pytest
from moto import mock_aws

os.environ.update({
    "AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "x", "AWS_DEFAULT_REGION": "ap-south-1",
    "SUPPLIERS_TABLE": "t-Suppliers", "VERIFICATION_LOG_TABLE": "t-Log", "STATS_TABLE": "t-Stats",
    "BADGES_TABLE": "t-Badges",
})

TOKENS = {"tok-alice": "alice", "tok-bob": "bob"}


def mod(name):
    return importlib.import_module(f"lambda.{name}")


def ev(token="tok-alice", body=None, params=None, path=None):
    e = {"headers": {"Authorization": f"Bearer {token}"} if token else {}}
    if body is not None:
        e["body"] = json.dumps(body)
    if params:
        e["queryStringParameters"] = params
    if path:
        e["pathParameters"] = path
    return e


def call(module, **kw):
    r = mod(module).handler(ev(**kw), None)
    return r["statusCode"], json.loads(r["body"])


@pytest.fixture()
def env(monkeypatch):
    with mock_aws():
        db = boto3.client("dynamodb")
        def make(name, hash_key, range_key=None):
            keys = [{"AttributeName": hash_key, "KeyType": "HASH"}]
            attrs = [{"AttributeName": hash_key, "AttributeType": "S"}]
            if range_key:
                keys.append({"AttributeName": range_key, "KeyType": "RANGE"})
                attrs.append({"AttributeName": range_key, "AttributeType": "S"})
            db.create_table(TableName=name, KeySchema=keys, AttributeDefinitions=attrs,
                            BillingMode="PAY_PER_REQUEST")
        make("t-Suppliers", "owner_user_id", "gstin")
        make("t-Log", "owner_user_id", "log_id")
        make("t-Stats", "stat_id")
        make("t-Badges", "badge_id")

        common = mod("common")
        def fake_get_user(AccessToken):
            if AccessToken not in TOKENS:
                from botocore.exceptions import ClientError
                raise ClientError({"Error": {"Code": "NotAuthorizedException", "Message": "bad"}}, "GetUser")
            return {"Username": TOKENS[AccessToken]}
        monkeypatch.setattr(common.cognito, "get_user", fake_get_user)

        # Fake the live lookup: any structurally valid GSTIN "exists" and is Active, old.
        def fake_verify(gstin, trace=None):
            from agents.verification_agent import VerificationResult
            from agents.gstin_utils import validate_gstin
            from agents.trace import step
            if not validate_gstin(gstin).is_valid:
                step(trace, "verification", "format check FAILED", ok=False)
                return VerificationResult(gstin, "failed", "offline_validation",
                                          error="invalid_gstin:checksum_mismatch")
            step(trace, "verification", "gstincheck.co.in lookup: record found", ok=True, started=None)
            return VerificationResult(gstin, "degraded", "gstincheck.co.in", data={"gstin_lookup": {
                "legal_name": "ACME SUPPLIES PRIVATE LIMITED", "registration_status": "Active",
                "raw": {"rgdt": "01/07/2017"}}})
        monkeypatch.setattr(mod("verify_supplier"), "verify_gstin", fake_verify)
        yield


def test_unauthenticated_requests_get_401(env):
    for m in ("get_suppliers", "get_history", "demo_seed"):
        assert call(m, token=None)[0] == 401
    assert call("get_report", token="nope", path={"log_id": "x"})[0] == 401


def test_verify_persists_and_returns_assessment_trace_and_log_id(env):
    code, out = call("verify_supplier", body={"gstin": "29AAACI4798L1ZU"})
    assert code == 200
    assert out["assessment"]["verdict"] == "VERIFIED"
    assert out["trace"] and out["log_id"]
    _, sup = call("get_suppliers")
    assert sup["meta"]["total"] == 1 and sup["data"][0]["name"] == "ACME SUPPLIES PRIVATE LIMITED"


def test_two_users_see_two_different_supplier_lists(env):
    call("verify_supplier", token="tok-alice", body={"gstin": "29AAACI4798L1ZU"})
    call("verify_supplier", token="tok-bob", body={"gstin": "07AAACT2727Q1ZY"})
    a = call("get_suppliers", token="tok-alice")[1]["data"]
    b = call("get_suppliers", token="tok-bob")[1]["data"]
    assert [s["gstin"] for s in a] == ["29AAACI4798L1ZU"]
    assert [s["gstin"] for s in b] == ["07AAACT2727Q1ZY"]


def test_report_is_owner_scoped_and_404_not_403(env):
    _, out = call("verify_supplier", token="tok-alice", body={"gstin": "29AAACI4798L1ZU"})
    assert call("get_report", token="tok-alice", path={"log_id": out["log_id"]})[0] == 200
    code, body = call("get_report", token="tok-bob", path={"log_id": out["log_id"]})
    assert code == 404 and body["error"] == "not_found"


def test_invalid_gstin_is_logged_as_reject_but_not_saved_as_supplier(env):
    _, out = call("verify_supplier", body={"gstin": "27AAPFU0939F1ZX"})
    assert out["assessment"]["verdict"] == "REJECT" and out["assessment"]["trust_score"] == 0
    assert call("get_suppliers")[1]["meta"]["total"] == 0
    hist = call("get_history")[1]
    assert hist["meta"]["total"] == 1 and hist["data"][0]["verdict"] == "REJECT"


def test_second_supplier_sharing_a_bank_account_flips_the_first_to_risk(env):
    call("verify_supplier", body={"gstin": "29AAAPL1234C1ZA", "bank_account": "0001 1122 2333"})
    assert call("get_suppliers")[1]["data"][0]["verdict"] == "VERIFIED"
    _, out = call("verify_supplier", body={"gstin": "33AABCT3518Q1Z3", "bank_account": "000111222333"})
    assert out["assessment"]["verdict"] == "RISK"
    verdicts = {s["gstin"]: s["verdict"] for s in call("get_suppliers")[1]["data"]}
    assert verdicts == {"29AAAPL1234C1ZA": "RISK", "33AABCT3518Q1Z3": "RISK"}   # the OLD one flipped too


def test_reverify_without_bank_account_keeps_previously_declared_one(env):
    call("verify_supplier", body={"gstin": "29AAAPL1234C1ZA", "bank_account": "000111222333"})
    call("verify_supplier", body={"gstin": "33AABCT3518Q1Z3", "bank_account": "000111222333"})
    _, out = call("verify_supplier", body={"gstin": "29AAAPL1234C1ZA"})       # no bank in the request
    assert out["assessment"]["verdict"] == "RISK"


def test_seed_loads_4_real_plus_4_crafted_and_ring_is_detected_by_the_rules(env):
    code, out = call("demo_seed")
    assert code == 200 and out["data"]["seeded"] == 9
    assert out["meta"]["by_verdict"] == {"VERIFIED": 5, "RISK": 3, "REVIEW": 1}
    rows = {s["gstin"]: s for s in call("get_suppliers")[1]["data"]}
    assert rows["07AAACT2727Q1ZY"]["verdict"] == "REVIEW"                      # shell company
    for g in ("29AAAPL1234C1ZA", "33AABCT3518Q1Z3", "06AAFCS1234D1ZV"):        # ring
        assert rows[g]["verdict"] == "RISK"
        assert any(f["code"] == "shared_bank_account" for f in rows[g]["findings"])
    assert all(r["data_origin"] in ("live_api", "crafted_test_case") for r in rows.values())


def test_seed_is_idempotent(env):
    call("demo_seed"); call("demo_seed")
    assert call("get_suppliers")[1]["meta"]["total"] == 9
    assert call("get_history")[1]["meta"]["total"] == 9


def test_history_sorted_newest_first_and_filterable(env):
    call("verify_supplier", body={"gstin": "29AAACI4798L1ZU"})
    call("verify_supplier", body={"gstin": "07AAACT2727Q1ZY"})
    h = call("get_history")[1]["data"]
    assert h[0]["timestamp"] >= h[1]["timestamp"]
    only = call("get_history", params={"gstin": "07"})[1]["data"]
    assert [r["gstin"] for r in only] == ["07AAACT2727Q1ZY"]
    assert call("get_history", params={"limit": "abc"})[0] == 400


def test_counter_is_real_and_public(env):
    assert call("get_stats", token=None)[1]["data"]["total_checks"] == 0
    call("verify_supplier", body={"gstin": "29AAACI4798L1ZU"})
    call("verify_supplier", token="tok-bob", body={"gstin": "29AAACI4798L1ZU"})
    assert call("get_stats", token=None)[1]["data"]["total_checks"] == 2      # shared across users


def test_legacy_rows_without_risk_data_stay_unscored_not_falsely_verified(env):
    import boto3
    boto3.resource("dynamodb").Table("t-Suppliers").put_item(Item={
        "owner_user_id": "alice", "gstin": "29AAACI4798L1ZU", "legal_name": "OLD ROW",
        "status": "degraded", "source": "gstincheck.co.in", "last_verified_at": 1})
    call("verify_supplier", body={"gstin": "07AAACT2727Q1ZY"})           # triggers a refresh over all rows
    rows = {s["gstin"]: s for s in call("get_suppliers")[1]["data"]}
    assert rows["29AAACI4798L1ZU"]["verdict"] == ""                      # UI shows "Not scored"
    assert rows["07AAACT2727Q1ZY"]["verdict"] == "VERIFIED"


# ---------- public badges + nightly re-check ----------

class _Ctx:
    def get_remaining_time_in_millis(self):
        return 300_000


def _make_badge(token="tok-alice", gstin="29AAACI4798L1ZU", **declared):
    _, out = call("verify_supplier", token=token, body={"gstin": gstin, **declared})
    code, body = call("create_badge", token=token, body={"log_id": out["log_id"]})
    return out, code, body


def test_badge_is_public_and_exposes_only_safe_fields(env):
    _, code, body = _make_badge(bank_account="000111222333")
    assert code == 200
    code, pub = call("get_badge", token=None, path={"badge_id": body["data"]["badge_id"]})   # no auth
    assert code == 200
    assert set(pub["data"]) == {"badge_id", "gstin", "name", "registration_status", "trust_score", "verified_at"}
    assert "000111222333" not in json.dumps(pub)


def test_badge_is_only_issued_to_verified_suppliers(env):
    call("verify_supplier", body={"gstin": "29AAAPL1234C1ZA", "bank_account": "000111222333"})
    _, out = call("verify_supplier", body={"gstin": "33AABCT3518Q1Z3", "bank_account": "000111222333"})   # ring -> RISK
    code, body = call("create_badge", body={"log_id": out["log_id"]})
    assert code == 400 and body["error"] == "badge_only_for_verified"


def test_badge_cannot_be_created_from_someone_elses_report(env):
    _, out = call("verify_supplier", token="tok-alice", body={"gstin": "29AAACI4798L1ZU"})
    code, body = call("create_badge", token="tok-bob", body={"log_id": out["log_id"]})
    assert code == 404 and body["error"] == "not_found"
    assert call("get_badge", token=None, path={"badge_id": "doesnotexist"})[0] == 404


def test_reissuing_a_badge_reuses_the_same_link(env):
    _, _, first = _make_badge()
    _, _, second = _make_badge()
    assert first["data"]["badge_id"] == second["data"]["badge_id"]


def test_nightly_recheck_revokes_badge_when_supplier_stops_being_verified(env, monkeypatch):
    monkeypatch.setattr(mod("recheck_all"), "verify_gstin", mod("verify_supplier").verify_gstin)
    _, _, b = _make_badge(gstin="29AAAPL1234C1ZA", bank_account="000111222333")
    badge_id = b["data"]["badge_id"]
    # A second supplier appears sharing the bank account: A flips to RISK, but its badge is stale until the re-check.
    call("verify_supplier", body={"gstin": "33AABCT3518Q1Z3", "bank_account": "000111222333"})
    assert call("get_badge", token=None, path={"badge_id": badge_id})[0] == 200

    result = mod("recheck_all").handler({}, _Ctx())
    summary = json.loads(result["body"])
    assert summary["checked"] == 2 and summary["errors"] == 0
    assert call("get_badge", token=None, path={"badge_id": badge_id})[0] == 404    # revoked


def test_nightly_recheck_keeps_badge_and_logs_a_scheduled_entry(env, monkeypatch):
    monkeypatch.setattr(mod("recheck_all"), "verify_gstin", mod("verify_supplier").verify_gstin)
    _, _, b = _make_badge()
    before = call("get_history")[1]["meta"]["total"]
    mod("recheck_all").handler({}, _Ctx())
    assert call("get_badge", token=None, path={"badge_id": b["data"]["badge_id"]})[0] == 200
    hist = call("get_history")[1]
    assert hist["meta"]["total"] == before + 1
    # both logs can share the same second, so look through all of them rather than trusting the order
    traces = [call("get_report", path={"log_id": h["log_id"]})[1]["data"]["trace"] for h in hist["data"]]
    assert sum(any(t["agent"] == "scheduler" for t in tr) for tr in traces) == 1


def test_nightly_recheck_skips_crafted_cases_and_survives_a_dead_live_source(env, monkeypatch):
    from agents.verification_agent import VerificationResult
    call("demo_seed")
    monkeypatch.setattr(mod("recheck_all"), "verify_gstin",
                        lambda gstin, trace=None: VerificationResult(gstin, "failed", "none", error="all_gstin_lookups_failed:timeout"))
    before = {s["gstin"]: (s["verdict"], s["trust_score"]) for s in call("get_suppliers")[1]["data"]}
    summary = json.loads(mod("recheck_all").handler({}, _Ctx())["body"])
    assert summary["candidates"] == 5 and summary["skipped"] == 5 and summary["checked"] == 0   # 4 crafted never touched
    after = {s["gstin"]: (s["verdict"], s["trust_score"]) for s in call("get_suppliers")[1]["data"]}
    assert before == after                                                                       # nothing overwritten

from datetime import date

from agents import risk_agent as ra

TODAY = date(2026, 9, 19)


def lookup_result(status="Active", rgdt="01/07/2017", legal_name="INFOSYS LIMITED"):
    return {
        "gstin": "29AAACI4798L1ZU", "status": "degraded", "source": "gstincheck.co.in", "error": None,
        "data": {"gstin_lookup": {"legal_name": legal_name, "registration_status": status,
                                  "raw": {"rgdt": rgdt}}},
    }


def codes(findings):
    return [f["code"] for f in findings]


def test_clean_established_supplier_is_verified():
    a = ra.build_assessment(ra.own_findings(lookup_result(), {}, today=TODAY))
    assert a["verdict"] == "VERIFIED"
    assert a["trust_score"] == 100


def test_invalid_gstin_is_reject_with_zero_score():
    failed = {"gstin": "27AAPFU0939F1ZX", "status": "failed", "source": "offline_validation",
              "data": {}, "error": "invalid_gstin:checksum_mismatch:expected_V_got_X"}
    a = ra.build_assessment(ra.own_findings(failed, {}, today=TODAY))
    assert a["verdict"] == "REJECT"
    assert a["trust_score"] == 0
    assert "invalid_gstin" in codes(a["findings"])


def test_lookup_outage_is_review_not_reject():
    failed = {"gstin": "29AAAPL1234C1ZA", "status": "failed", "source": "none",
              "data": {}, "error": "all_gstin_lookups_failed:gstincheck.co.in rate-limited"}
    a = ra.build_assessment(ra.own_findings(failed, {}, today=TODAY))
    assert a["verdict"] == "REVIEW"
    assert a["trust_score"] == 70


def test_cancelled_gstin_is_risk():
    a = ra.build_assessment(ra.own_findings(lookup_result(status="Cancelled"), {}, today=TODAY))
    assert a["verdict"] == "RISK"


def test_shell_company_is_review():
    declared = {"registered_date": "2026-08-15", "paid_up_capital_inr": 10000,
                "claimed_business": "Bulk steel, 50Cr turnover"}
    a = ra.build_assessment(ra.own_findings(None, declared, today=TODAY))
    assert a["verdict"] == "REVIEW"
    assert {"very_recently_registered", "minimal_paid_up_capital"} <= set(codes(a["findings"]))
    assert a["trust_score"] == 50


def test_old_company_is_not_flagged_as_recent():
    assert "very_recently_registered" not in codes(
        ra.own_findings(lookup_result(rgdt="01/07/2017"), {}, today=TODAY))


def test_name_mismatch_flagged_but_close_names_pass():
    assert "name_mismatch" in codes(
        ra.own_findings(lookup_result(), {"name": "Random Traders"}, today=TODAY))
    assert "name_mismatch" not in codes(
        ra.own_findings(lookup_result(), {"name": "Infosys Ltd"}, today=TODAY))


def test_ring_detects_shared_bank_account_and_masks_it():
    peers = [
        {"gstin": "B", "legal_name": "Sunrise Traders", "bank_account": "0001 1122 2333", "address": "x"},
        {"gstin": "C", "legal_name": "New Sunrise Impex", "bank_account": "000111222333", "address": "y"},
        {"gstin": "D", "legal_name": "Unrelated", "bank_account": "999", "address": "z"},
    ]
    f = ra.ring_findings("A", {"bank_account": "000111222333"}, peers)
    assert codes(f) == ["shared_bank_account"]
    assert "2 other" in f[0]["message"]
    assert "000111222333" not in f[0]["message"]          # never leak the full number
    assert ra.build_assessment(f)["verdict"] == "RISK"


def test_ring_ignores_self_and_empty_bank():
    peers = [{"gstin": "A", "bank_account": "123456"}]
    assert ra.ring_findings("A", {"bank_account": "123456"}, peers) == []
    assert ra.ring_findings("A", {}, [{"gstin": "B", "bank_account": ""}]) == []


def test_shared_address_is_review():
    peers = [{"gstin": "B", "address": "Plot 14, Industrial Area"}]
    f = ra.ring_findings("A", {"address": "plot 14 industrial area"}, peers)
    assert codes(f) == ["shared_address"]
    assert ra.build_assessment(f)["verdict"] == "REVIEW"


def test_worst_severity_wins_and_score_clamped():
    findings = [ra._finding("a", "review", "", 80), ra._finding("b", "risk", "", 80)]
    a = ra.build_assessment(findings)
    assert a["verdict"] == "RISK" and a["trust_score"] == 0

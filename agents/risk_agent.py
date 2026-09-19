"""
risk_agent.py - rule-based risk scoring on top of verification_agent.

Deliberately deterministic (no LLM in the decision path): every finding comes
from a named rule, so a judge can ask "why is this RISK?" and get an exact
answer. Two layers:

  own_findings()   - rules that need only this one supplier (checksum, GST
                     status, company age, paid-up capital, declared-vs-
                     registered name).
  ring_findings()  - rules that compare this supplier against the owner's
                     other suppliers (shared bank account / shared address).
                     This is what feeds the fraud-network graph.

build_assessment() folds a list of findings into {trust_score, verdict,
findings}. Verdict is the WORST severity present; score is 100 minus the sum
of penalties (clamped 0-100), so the two can be explained independently.

Verdicts: VERIFIED | REVIEW | RISK | REJECT
"""

import difflib
import re
from datetime import date, datetime

from agents.trace import step, now

SEVERITY_RANK = {"info": 0, "review": 1, "risk": 2, "reject": 3}
VERDICT_BY_SEVERITY = {
    "info": "VERIFIED",
    "review": "REVIEW",
    "risk": "RISK",
    "reject": "REJECT",
}

# Thresholds live at the top so they are easy to defend and easy to tune.
VERY_RECENT_DAYS = 90
RECENT_DAYS = 365
MIN_PAID_UP_CAPITAL_INR = 100_000  # Rs 1 lakh

_NAME_NOISE = {"limited", "ltd", "pvt", "private", "llp", "inc", "co", "company", "the"}


def _finding(code, severity, message, penalty=0):
    return {"code": code, "severity": severity, "message": message, "penalty": penalty}


# ---------- helpers ----------

def parse_date(value):
    """Accepts 'dd/mm/yyyy' (GST portal format) or ISO 'yyyy-mm-dd'. None if unparseable."""
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _norm_name(name):
    tokens = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    return " ".join(t for t in tokens if t not in _NAME_NOISE)


def names_match(a, b):
    a, b = _norm_name(a), _norm_name(b)
    if not a or not b:
        return True  # nothing to compare - don't raise a finding on missing data
    if a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


def norm_bank(value):
    return re.sub(r"\D", "", value or "")


def norm_address(value):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (value or "").lower()).split())


def mask_bank(value):
    digits = norm_bank(value)
    return "****" + digits[-4:] if len(digits) >= 4 else "****"


def _lookup_of(gstin_result):
    return ((gstin_result or {}).get("data") or {}).get("gstin_lookup") or {}


# ---------- layer 1: single-supplier rules ----------

def own_findings(gstin_result, declared, today=None, trace=None):
    """
    gstin_result: VerificationResult.to_dict(), or None for records that never
                  went through the live pipeline (crafted test cases).
    declared:     dict of what the user typed / the seed file says. Optional keys:
                  name, bank_account, address, registered_date,
                  paid_up_capital_inr, claimed_business.
    """
    started = now()
    today = today or date.today()
    declared = declared or {}
    findings = []

    # Rule 1: offline format + checksum failure is an immediate REJECT.
    if gstin_result and gstin_result.get("status") == "failed":
        err = gstin_result.get("error") or ""
        if err.startswith("invalid_gstin:"):
            findings.append(_finding(
                "invalid_gstin", "reject",
                f"GSTIN failed offline validation ({err.split(':', 1)[1]}). "
                "It cannot belong to a real taxpayer.", 100))
            step(trace, "risk", "Rules evaluated: invalid GSTIN -> REJECT", ok=False, started=started)
            return findings
        findings.append(_finding(
            "live_lookup_unavailable", "review",
            "No live GST source could confirm this GSTIN, so it is unverified "
            "(it may not exist, or the data providers were unreachable).", 30))

    lookup = _lookup_of(gstin_result)

    # Rule 2: GST registration status.
    reg_status = (lookup.get("registration_status") or "").strip()
    if reg_status and reg_status.upper() != "UNKNOWN":
        if reg_status.lower() == "active":
            findings.append(_finding("gstin_active", "info", "GSTIN is Active on the GST registry."))
        else:
            findings.append(_finding(
                "gstin_not_active", "risk",
                f"GSTIN registration status is '{reg_status}', not Active. "
                "Do not pay invoices against a non-active registration.", 60))

    # Rule 3: company age. Real registration date wins over anything declared.
    raw = lookup.get("raw") or {}
    reg_date = parse_date(raw.get("rgdt")) or parse_date(declared.get("registered_date"))
    if reg_date:
        age_days = (today - reg_date).days
        if age_days < VERY_RECENT_DAYS:
            findings.append(_finding(
                "very_recently_registered", "review",
                f"Registered only {age_days} days ago ({reg_date.isoformat()}). "
                "Newly created entities are the most common shell-company pattern.", 30))
        elif age_days < RECENT_DAYS:
            findings.append(_finding(
                "recently_registered", "review",
                f"Registered {age_days} days ago ({reg_date.isoformat()}) - under a year of history.", 15))
        else:
            findings.append(_finding(
                "established_registration", "info",
                f"Registered {age_days // 365} year(s) ago ({reg_date.isoformat()})."))

    # Rule 4: paid-up capital vs. claimed scale.
    capital = declared.get("paid_up_capital_inr")
    if isinstance(capital, (int, float)) and capital < MIN_PAID_UP_CAPITAL_INR:
        claim = declared.get("claimed_business")
        extra = f" while claiming: \"{claim}\"" if claim else ""
        findings.append(_finding(
            "minimal_paid_up_capital", "review",
            f"Paid-up capital is only Rs {int(capital):,}{extra}. "
            "Business scale looks inconsistent with capital.", 20))

    # Rule 5: declared supplier name vs. the government-registered legal name.
    legal_name = lookup.get("legal_name")
    declared_name = declared.get("name")
    if declared_name and legal_name and legal_name != "UNKNOWN":
        if not names_match(declared_name, legal_name):
            findings.append(_finding(
                "name_mismatch", "review",
                f"You entered '{declared_name}' but the GST registry lists '{legal_name}'. "
                "This can be a trade name - confirm before paying.", 20))

    if gstin_result is None:
        findings.append(_finding(
            "crafted_test_case", "info",
            "Crafted test case (not a real registered entity) - used to exercise the risk rules."))

    step(trace, "risk",
         f"Single-supplier rules evaluated: {len(findings)} finding(s)",
         ok=True, started=started)
    return findings


# ---------- layer 2: cross-supplier (fraud-ring) rules ----------

def ring_findings(gstin, declared, peers, trace=None):
    """
    peers: the owner's OTHER suppliers as dicts with gstin, bank_account,
           address, and name / legal_name. Callers must not include the
           supplier itself (we also skip matching gstin defensively).
    """
    started = now()
    declared = declared or {}
    findings = []

    my_bank = norm_bank(declared.get("bank_account"))
    my_addr = norm_address(declared.get("address"))

    def label(p):
        return f"{p.get('legal_name') or p.get('name') or 'unnamed'} ({p.get('gstin')})"

    if my_bank:
        sharing = [p for p in peers
                   if p.get("gstin") != gstin and norm_bank(p.get("bank_account")) == my_bank]
        if sharing:
            findings.append(_finding(
                "shared_bank_account", "risk",
                f"Bank account {mask_bank(my_bank)} is also used by {len(sharing)} other "
                f"supplier(s): {', '.join(label(p) for p in sharing)}. "
                "Different 'companies' paid into one account is the classic fraud-ring signal.", 50))

    if my_addr:
        sharing = [p for p in peers
                   if p.get("gstin") != gstin and norm_address(p.get("address")) == my_addr]
        if sharing:
            findings.append(_finding(
                "shared_address", "review",
                f"Address is identical to {len(sharing)} other supplier(s): "
                f"{', '.join(label(p) for p in sharing)}.", 25))

    step(trace, "risk",
         f"Cross-supplier rules evaluated against {len(peers)} other supplier(s): "
         f"{len(findings)} finding(s)", ok=True, started=started)
    return findings


# ---------- fold findings into a verdict ----------

def build_assessment(findings):
    worst = "info"
    total_penalty = 0
    for f in findings:
        if SEVERITY_RANK[f["severity"]] > SEVERITY_RANK[worst]:
            worst = f["severity"]
        total_penalty += f.get("penalty", 0)

    score = max(0, min(100, 100 - total_penalty))
    if worst == "reject":
        score = 0

    return {
        "trust_score": int(score),
        "verdict": VERDICT_BY_SEVERITY[worst],
        "findings": findings,
    }

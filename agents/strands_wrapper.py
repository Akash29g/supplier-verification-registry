"""
strands_wrapper.py

Exposes the verification pipeline as Strands Agents SDK tools. This is the
"Build It" track credit called out in the master plan - deliberately small
and self-contained. Do not expand this file beyond wrapping existing
functions; the deployed AWS stack (Ship It) and the UI (Best UI) are the
actual build priorities, not this file.
"""

from strands import tool

from agents.verification_agent import verify_gstin
from agents.gstin_utils import validate_gstin


@tool
def check_gstin_format(gstin: str) -> dict:
    """Offline structural + checksum validation of a GSTIN. No network call."""
    result = validate_gstin(gstin)
    return result.to_dict()


@tool
def verify_supplier_gstin(gstin: str) -> dict:
    """
    Run the full verification pipeline against a GSTIN: offline format check,
    then live lookup via gstincheck.co.in (with data.gov.in cross-reference
    where available). Returns a structured result, never raises.
    """
    result = verify_gstin(gstin)
    return result.to_dict()

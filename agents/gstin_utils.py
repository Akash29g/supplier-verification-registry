"""
GSTIN validation utilities.

GSTIN structure (15 characters):
  [0:2]   State code (2 digits)
  [2:12]  PAN (10 chars: 5 letters, 4 digits, 1 letter)
  [12]    Entity number for this PAN in this state (1-9 or A-Z)
  [13]    Fixed 'Z'
  [14]    Checksum character (Luhn mod-36 over the first 14 chars)

No network calls in this module - pure offline validation. This is the
foundation everything else (verification_agent, risk scoring) sits on top of,
so it needs to be correct and fast before any real API is wired in.
"""

import re

CODE_POINT_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

GSTIN_FORMAT_RE = re.compile(
    r"^([0-9]{2})([A-Z]{5}[0-9]{4}[A-Z])([1-9A-Z])(Z)([0-9A-Z])$"
)


class GstinValidationResult:
    """Structured result so callers don't have to parse a bool + string."""

    def __init__(self, is_valid, reason, state_code=None, pan=None):
        self.is_valid = is_valid
        self.reason = reason
        self.state_code = state_code
        self.pan = pan

    def to_dict(self):
        return {
            "is_valid": self.is_valid,
            "reason": self.reason,
            "state_code": self.state_code,
            "pan": self.pan,
        }

    def __repr__(self):
        return f"GstinValidationResult(is_valid={self.is_valid}, reason={self.reason!r})"


def calculate_checksum(gstin_first_14):
    """
    Luhn mod-36 checksum used by GSTN.

    Walk the first 14 characters right-to-left, alternating a multiplication
    factor of 2 and 1, folding each product back into base-36 (divmod by 36
    and adding quotient + remainder), summing everything, then taking the
    36's-complement of the total mod 36 as the check character's index.
    """
    factor = 2
    total = 0
    n = len(CODE_POINT_CHARS)

    for char in reversed(gstin_first_14):
        code_point = CODE_POINT_CHARS.index(char)
        product = factor * code_point
        total += (product // n) + (product % n)
        factor = 1 if factor == 2 else 2

    remainder = total % n
    check_code_point = (n - remainder) % n
    return CODE_POINT_CHARS[check_code_point]


def validate_gstin(raw_gstin):
    """
    Full validation: format shape, allowed characters, and checksum.
    Returns a GstinValidationResult - never raises on bad input, since this
    sits directly in front of user-entered / bulk-uploaded data.
    """
    if not raw_gstin or not isinstance(raw_gstin, str):
        return GstinValidationResult(False, "empty_or_not_a_string")

    gstin = raw_gstin.strip().upper()

    if len(gstin) != 15:
        return GstinValidationResult(False, f"wrong_length:{len(gstin)}")

    match = GSTIN_FORMAT_RE.match(gstin)
    if not match:
        return GstinValidationResult(False, "malformed_structure")

    state_code, pan, entity_code, _, checksum_char = match.groups()

    expected_checksum = calculate_checksum(gstin[:14])
    if checksum_char != expected_checksum:
        return GstinValidationResult(
            False,
            f"checksum_mismatch:expected_{expected_checksum}_got_{checksum_char}",
            state_code=state_code,
            pan=pan,
        )

    return GstinValidationResult(True, "valid", state_code=state_code, pan=pan)


if __name__ == "__main__":
    # quick manual smoke test - not a substitute for tests/test_gstin_utils.py
    samples = ["27AAPFU0939F1ZV", "27AAPFU0939F1ZX", "not-a-gstin", ""]
    for s in samples:
        print(s, "->", validate_gstin(s))

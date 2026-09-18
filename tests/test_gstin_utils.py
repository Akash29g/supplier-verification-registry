import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.gstin_utils import validate_gstin, calculate_checksum


# Known-good GSTINs (checksum computed correctly, real structure).
# 27AAPFU0939F1ZV is a widely-used public reference GSTIN in tutorials/docs -
# useful as a sanity anchor since its checksum is independently verifiable.
VALID_GSTINS = [
    "27AAPFU0939F1ZV",
    "29AAAPL1234C1ZA",
    "07AAACT2727Q1ZY",
    "33AABCT3518Q1Z3",
    "06AAFCS1234D1ZV",
]

# Known-bad GSTINs, one failure mode each - this is what Round 1's failure
# handling has to survive without crashing the pipeline.
INVALID_CASES = {
    "27AAPFU0939F1ZX": "checksum_mismatch",   # last char tampered
    "27AAPFU0939F1Z": "wrong_length",         # one char short
    "27AAPFU0939F1ZVV": "wrong_length",       # one char too long
    "AAAAAAAAAAAAAAA": "malformed_structure", # right length, wrong shape
    "": "empty_or_not_a_string",
    None: "empty_or_not_a_string",
    "27aapfu0939f1zv": None,  # lowercase input - should normalize and pass
}


def test_valid_gstins_pass():
    for gstin in VALID_GSTINS:
        result = validate_gstin(gstin)
        assert result.is_valid, f"expected {gstin} to be valid, got {result.reason}"


def test_invalid_gstins_fail_with_expected_reason():
    for gstin, expected_reason_prefix in INVALID_CASES.items():
        if expected_reason_prefix is None:
            continue
        result = validate_gstin(gstin)
        assert not result.is_valid, f"expected {gstin!r} to be invalid"
        assert result.reason.startswith(expected_reason_prefix), (
            f"{gstin!r}: expected reason starting with {expected_reason_prefix!r}, "
            f"got {result.reason!r}"
        )


def test_lowercase_input_is_normalized():
    result = validate_gstin("27aapfu0939f1zv")
    assert result.is_valid, f"lowercase input should normalize and pass, got {result.reason}"


def test_checksum_function_matches_known_reference():
    # 27AAPFU0939F1Z + checksum should equal V for the reference GSTIN
    assert calculate_checksum("27AAPFU0939F1Z") == "V"


def test_never_raises_on_garbage_input():
    garbage_inputs = [12345, [], {}, "💥💥💥", " " * 15]
    for g in garbage_inputs:
        try:
            result = validate_gstin(g)
        except Exception as e:
            raise AssertionError(f"validate_gstin raised on {g!r}: {e}")
        assert result.is_valid is False


if __name__ == "__main__":
    test_valid_gstins_pass()
    test_invalid_gstins_fail_with_expected_reason()
    test_lowercase_input_is_normalized()
    test_checksum_function_matches_known_reference()
    test_never_raises_on_garbage_input()
    print("All GSTIN validator tests passed.")

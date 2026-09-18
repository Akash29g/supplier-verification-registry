"""
Run this locally, once you have real GSTINs to test with:

    python3 data/fetch_seed_legit.py 27AAPFU0939F1ZV 29AAAPL1234C1ZA ...

It calls the real verification_agent (which hits gstincheck.co.in and
data.gov.in with your keys from .env) for each GSTIN you pass in, and writes
the results into data/seed_legit.json tagged source: live_api.

Note: this script needs network access and your real .env keys - it's meant
to be run on your machine, not in this container. Pick 5-10 real, currently
registered GSTINs (your own business, a few well-known companies' public
GSTINs, etc.) to seed with.
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from agents.verification_agent import verify_gstin

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "seed_legit.json")


def main(gstins):
    results = []
    for gstin in gstins:
        print(f"Verifying {gstin} ...")
        result = verify_gstin(gstin)
        entry = result.to_dict()
        entry["source"] = "live_api"
        results.append(entry)
        if result.status == "failed":
            print(f"  WARNING: {gstin} failed - {result.error}")
        else:
            print(f"  OK - status={result.status}, source={result.source}")

    with open(OUTPUT_PATH, "w") as f:
        json.dump({"_note": "Real, live-fetched results tagged source: live_api. See fetch_seed_legit.py.", "companies": results}, f, indent=2)

    print(f"\nWrote {len(results)} entries to {OUTPUT_PATH}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 data/fetch_seed_legit.py GSTIN1 GSTIN2 ...")
        sys.exit(1)
    main(sys.argv[1:])

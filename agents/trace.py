"""
trace.py - tiny helper so every agent can record what it actually did.

The trace is a list of {agent, message, ok, ms} dicts built while the pipeline
runs. It is returned to the frontend as part of the /verify response and
persisted on the VerificationLog row, so /verify/<id> can show the exact same
steps later. Nothing in here is decorative: a line only exists if the step ran.
"""

import time


def now():
    return time.time()


def step(trace, agent, message, ok=True, started=None, neutral=False):
    """Append one step. `trace=None` is allowed so callers can skip tracing.

    neutral=True marks an informational step (e.g. an optional cross-check that
    found nothing) so the UI shows a grey dash instead of a red cross.
    """
    if trace is None:
        return
    entry = {
        "agent": agent,
        "message": message,
        "ok": bool(ok),
        "ms": int((time.time() - started) * 1000) if started else None,
    }
    if neutral:
        entry["neutral"] = True
    trace.append(entry)

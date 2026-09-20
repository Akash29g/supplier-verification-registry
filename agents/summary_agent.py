"""
Turns risk_agent's findings into a short plain-English paragraph via Bedrock.
Never blocks the pipeline: any failure falls back to a deterministic template.
"""
import json
import os

import boto3
from botocore.exceptions import ClientError

from agents.trace import step, now

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "apac.amazon.nova-lite-v1:0")
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "ap-south-1"))

_client = None


def _bedrock():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    return _client


def _fallback(name, verdict, trust_score, findings):
    if not findings:
        return f"{name or 'This supplier'} has no notable findings. Verdict: {verdict} ({trust_score}/100)."
    order = {"reject": 0, "risk": 1, "review": 2, "info": 3}
    top = sorted(findings, key=lambda f: order[f["severity"]])[:3]
    return f"{name or 'This supplier'} is rated {verdict} ({trust_score}/100). Key findings: " + "; ".join(f["message"] for f in top)


def summarize(name, verdict, trust_score, findings, trace=None):
    started = now()
    prompt = (
        "You are a supplier-risk analyst. In 2-3 plain-English sentences, summarise this "
        "assessment for someone deciding whether to pay this supplier.\n\n"
        f"Supplier: {name or 'unknown'}\nVerdict: {verdict}\nTrust score: {trust_score}/100\nFindings:\n"
        + "\n".join(f"- ({f['severity']}) {f['message']}" for f in findings)
        + "\n\nDo not invent facts beyond what's listed. Be direct."
    )
    try:
        resp = _bedrock().invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps({
                "messages": [{"role": "user", "content": [{"text": prompt}]}],
                "inferenceConfig": {"maxTokens": 220, "temperature": 0.3},
            }),
        )
        payload = json.loads(resp["body"].read())
        text = payload["output"]["message"]["content"][0]["text"].strip()
        step(trace, "summary", "Bedrock (Nova Lite) summary generated", ok=True, started=started)
        return text, "bedrock"
    except (ClientError, Exception) as e:
        step(trace, "summary", f"Bedrock unavailable ({type(e).__name__}), used template fallback", ok=False, started=started)
        return _fallback(name, verdict, trust_score, findings), "fallback"
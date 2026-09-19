import json
import os
import time
import uuid
import boto3
from botocore.exceptions import ClientError

from agents.verification_agent import verify_gstin

cognito = boto3.client("cognito-idp")
dynamodb = boto3.resource("dynamodb")

SUPPLIERS_TABLE = os.environ["SUPPLIERS_TABLE"]
VERIFICATION_LOG_TABLE = os.environ["VERIFICATION_LOG_TABLE"]


def _get_user_id(event):
    """Validate the bearer token against Cognito and return the user's sub."""
    headers = event.get("headers") or {}
    auth_header = headers.get("Authorization") or headers.get("authorization") or ""
    if not auth_header.startswith("Bearer "):
        return None
    access_token = auth_header.replace("Bearer ", "", 1).strip()
    try:
        result = cognito.get_user(AccessToken=access_token)
        return result["Username"]
    except ClientError:
        return None


def handler(event, context):
    user_id = _get_user_id(event)
    if not user_id:
        return _response(401, {"error": "missing_or_invalid_token"})

    try:
        body = json.loads(event.get("body") or "{}")
        gstin = (body.get("gstin") or "").strip().upper()
        if not gstin:
            return _response(400, {"error": "gstin_required"})

        result = verify_gstin(gstin)
        result_dict = result.to_dict()

        now = int(time.time())
        log_id = str(uuid.uuid4())

        suppliers_table = dynamodb.Table(SUPPLIERS_TABLE)
        log_table = dynamodb.Table(VERIFICATION_LOG_TABLE)

        suppliers_table.put_item(Item={
            "owner_user_id": user_id,
            "gstin": gstin,
            "legal_name": (result_dict.get("data") or {}).get("gstin_lookup", {}).get("legal_name", ""),
            "status": result_dict["status"],
            "source": result_dict["source"],
            "last_verified_at": now,
        })

        log_table.put_item(Item={
            "owner_user_id": user_id,
            "log_id": log_id,
            "gstin": gstin,
            "status": result_dict["status"],
            "source": result_dict["source"] or "none",
            "error": result_dict.get("error") or "",
            "timestamp": now,
            "raw_result": json.dumps(result_dict),
        })

        return _response(200, result_dict)

    except ClientError as e:
        return _response(500, {"error": "dynamodb_write_failed", "detail": e.response["Error"]["Message"]})
    except Exception as e:
        return _response(500, {"error": "unexpected_error", "detail": str(e)})


def _response(status_code, body_dict):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json","Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body_dict),
    }
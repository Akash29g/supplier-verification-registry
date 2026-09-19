"""
Shared helpers for the API Lambdas that ship with CodeUri ../ (verify, read
endpoints, demo seed). The three auth Lambdas use CodeUri ../lambda/ and do
not import this.

Import from a handler as:   from . import common
(`lambda` is a Python keyword, so `from lambda import ...` is a syntax error;
a relative import works because Lambda loads handlers as `lambda.<module>`.)
"""

import decimal
import json

import boto3
from botocore.exceptions import ClientError

cognito = boto3.client("cognito-idp")


def get_user_id(event):
    """Validate the bearer token against Cognito and return the user's id, else None."""
    headers = event.get("headers") or {}
    auth_header = headers.get("Authorization") or headers.get("authorization") or ""
    if not auth_header.startswith("Bearer "):
        return None
    access_token = auth_header.replace("Bearer ", "", 1).strip()
    try:
        return cognito.get_user(AccessToken=access_token)["Username"]
    except ClientError:
        return None


def _json_default(o):
    # DynamoDB hands numbers back as Decimal, which json can't serialise.
    if isinstance(o, decimal.Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    raise TypeError(f"not serialisable: {type(o)}")


def response(status_code, body_dict):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body_dict, default=_json_default),
    }


def ok(data, meta=None):
    """{ data, meta, error } envelope for the read endpoints."""
    return response(200, {"data": data, "meta": meta or {}, "error": None})


def fail(status_code, code):
    return response(status_code, {"data": None, "meta": {}, "error": code})

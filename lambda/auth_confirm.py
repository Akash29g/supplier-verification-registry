import json
import os
import boto3
from botocore.exceptions import ClientError

cognito = boto3.client("cognito-idp")
CLIENT_ID = os.environ["USER_POOL_CLIENT_ID"]


def handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
        email = body.get("email", "").strip().lower()
        code = body.get("code", "")

        if not email or not code:
            return _response(400, {"error": "email and code are required"})

        cognito.confirm_sign_up(
            ClientId=CLIENT_ID,
            Username=email,
            ConfirmationCode=code,
        )
        return _response(200, {"message": "Account confirmed. You can now log in."})

    except cognito.exceptions.CodeMismatchException:
        return _response(400, {"error": "invalid_code"})
    except cognito.exceptions.ExpiredCodeException:
        return _response(400, {"error": "code_expired"})
    except ClientError as e:
        return _response(500, {"error": "confirm_failed", "detail": e.response["Error"]["Message"]})


def _response(status_code, body_dict):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body_dict),
    }
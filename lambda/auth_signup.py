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
        password = body.get("password", "")

        if not email or not password:
            return _response(400, {"error": "email and password are required"})

        cognito.sign_up(
            ClientId=CLIENT_ID,
            Username=email,
            Password=password,
            UserAttributes=[{"Name": "email", "Value": email}],
        )

        return _response(201, {
            "message": "Signup successful. Check email for verification code.",
            "email": email,
        })

    except cognito.exceptions.UsernameExistsException:
        return _response(409, {"error": "account_already_exists"})
    except cognito.exceptions.InvalidPasswordException as e:
        return _response(400, {"error": "invalid_password", "detail": str(e)})
    except ClientError as e:
        return _response(500, {"error": "signup_failed", "detail": e.response["Error"]["Message"]})


def _response(status_code, body_dict):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json","Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body_dict),
    }
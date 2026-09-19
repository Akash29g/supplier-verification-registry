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

        result = cognito.initiate_auth(
            ClientId=CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": email, "PASSWORD": password},
        )
        auth = result["AuthenticationResult"]

        return _response(200, {
            "access_token": auth["AccessToken"],
            "id_token": auth["IdToken"],
            "refresh_token": auth["RefreshToken"],
            "expires_in": auth["ExpiresIn"],
        })

    except cognito.exceptions.NotAuthorizedException:
        return _response(401, {"error": "invalid_credentials"})
    except cognito.exceptions.UserNotConfirmedException:
        return _response(403, {"error": "account_not_confirmed"})
    except ClientError as e:
        return _response(500, {"error": "login_failed", "detail": e.response["Error"]["Message"]})


def _response(status_code, body_dict):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body_dict),
    }
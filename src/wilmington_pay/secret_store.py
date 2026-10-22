"""Secrets Manager access with a local env-var fallback for the POC."""
from __future__ import annotations

import json
import os
from functools import lru_cache


@lru_cache(maxsize=16)
def _fetch(secret_arn: str) -> str:
    import boto3  # available in the Lambda runtime

    return boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)["SecretString"]


def get_secret_json(secret_arn: str, env_prefix: str) -> dict:
    """Return a secret as a dict. Locally (no ARN) read ENV_PREFIX* variables,
    e.g. AUTHORIZENET_API_LOGIN_ID -> {"api_login_id": ...}."""
    if secret_arn:
        return json.loads(_fetch(secret_arn))
    return {k[len(env_prefix):].lower(): v for k, v in os.environ.items() if k.startswith(env_prefix)}


def get_secret_string(secret_arn: str) -> str:
    return _fetch(secret_arn)

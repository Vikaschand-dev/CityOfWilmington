"""DynamoDB Streams -> S3 archive for analytics (Athena / QuickSight) and audit.

Writes one JSON object per finished session to
  s3://$ARCHIVE_BUCKET/payments/dt=YYYY-MM-DD/<session_id>.json
The table holds no card data, so neither does the archive.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal

from wilmington_pay.models import SessionStatus

_TERMINAL = {s.value for s in SessionStatus if s.is_terminal}


def _plain(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v


def handler(event: dict, context=None) -> dict:
    import boto3
    from boto3.dynamodb.types import TypeDeserializer

    s3, deser, bucket = boto3.client("s3"), TypeDeserializer(), os.environ["ARCHIVE_BUCKET"]
    written = 0
    for record in event.get("Records", []):
        image = record.get("dynamodb", {}).get("NewImage")
        if record.get("eventName") not in {"INSERT", "MODIFY"} or not image:
            continue
        item = _plain({k: deser.deserialize(v) for k, v in image.items()})
        if item.get("status") not in _TERMINAL:
            continue
        day = time.strftime("%Y-%m-%d", time.gmtime(item.get("completed_at") or item["created_at"]))
        s3.put_object(
            Bucket=bucket,
            Key=f"payments/dt={day}/{item['session_id']}.json",
            Body=json.dumps(item).encode(),
            ContentType="application/json",
            ServerSideEncryption="aws:kms",
        )
        written += 1
    return {"archived": written}

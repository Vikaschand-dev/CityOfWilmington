"""Session persistence.

DynamoDB table design (see infra/template.yaml):
  PK  session_id
  GSI payment_code-index  (payment_code)   -> IVR lookup by one-time code
  GSI caller_ani-index    (caller_ani, created_at) -> IVR lookup by caller ID
  TTL ttl                 -> records age out; DynamoDB Streams archive them to S3 first
Only status/metadata is stored. No PAN, expiry or CVV - ever.
"""
from __future__ import annotations

import threading
import time
from typing import Iterable, Protocol

from .models import ACTIVE_STATUSES, PaymentSession, SessionStatus


class SessionRepository(Protocol):
    def put(self, session: PaymentSession) -> None: ...
    def get(self, session_id: str) -> PaymentSession | None: ...
    def find_active_by_code(self, code: str, now: float) -> PaymentSession | None: ...
    def find_active_by_ani(self, ani: str, now: float) -> PaymentSession | None: ...
    def code_in_use(self, code: str, now: float) -> bool: ...

    def transition(
        self, session_id: str, allowed_from: Iterable[SessionStatus], updates: dict
    ) -> PaymentSession | None:
        """Atomically apply `updates` iff current status is in `allowed_from`.
        Returns the updated session, or None if the precondition failed.
        This compare-and-set is what prevents a double charge."""
        ...

    def list_recent(self, limit: int = 100) -> list[PaymentSession]: ...


class InMemorySessionRepository:
    def __init__(self) -> None:
        self._items: dict[str, dict] = {}
        self._lock = threading.RLock()

    def put(self, session: PaymentSession) -> None:
        with self._lock:
            self._items[session.session_id] = session.to_item()

    def get(self, session_id: str) -> PaymentSession | None:
        with self._lock:
            item = self._items.get(session_id)
            return PaymentSession.from_item(item) if item else None

    def _active(self, now: float):
        for item in self._items.values():
            if SessionStatus(item["status"]) in ACTIVE_STATUSES and item["expires_at"] > now:
                yield item

    def find_active_by_code(self, code: str, now: float) -> PaymentSession | None:
        with self._lock:
            for item in self._active(now):
                if item["payment_code"] == code:
                    return PaymentSession.from_item(item)
        return None

    def find_active_by_ani(self, ani: str, now: float) -> PaymentSession | None:
        with self._lock:
            matches = [i for i in self._active(now) if i.get("caller_ani") == ani]
        if not matches:
            return None
        return PaymentSession.from_item(max(matches, key=lambda i: i["created_at"]))

    def code_in_use(self, code: str, now: float) -> bool:
        return self.find_active_by_code(code, now) is not None

    def transition(self, session_id, allowed_from, updates):
        allowed = {s.value for s in allowed_from}
        with self._lock:
            item = self._items.get(session_id)
            if not item or item["status"] not in allowed:
                return None
            for k, v in updates.items():
                item[k] = v.value if isinstance(v, SessionStatus) else v
            return PaymentSession.from_item(item)

    def list_recent(self, limit: int = 100) -> list[PaymentSession]:
        with self._lock:
            items = sorted(self._items.values(), key=lambda i: i["created_at"], reverse=True)[:limit]
        return [PaymentSession.from_item(i) for i in items]


class DynamoSessionRepository:
    def __init__(self, table_name: str) -> None:
        import boto3

        self._table = boto3.resource("dynamodb").Table(table_name)

    @staticmethod
    def _to_ddb(value):
        from decimal import Decimal

        if isinstance(value, float):
            return Decimal(str(value))
        if isinstance(value, list):
            return [DynamoSessionRepository._to_ddb(v) for v in value]
        if isinstance(value, dict):
            return {k: DynamoSessionRepository._to_ddb(v) for k, v in value.items()}
        return value

    def put(self, session: PaymentSession) -> None:
        self._table.put_item(
            Item=self._to_ddb(session.to_item()),
            ConditionExpression="attribute_not_exists(session_id)",
        )

    def get(self, session_id: str) -> PaymentSession | None:
        item = self._table.get_item(Key={"session_id": session_id}, ConsistentRead=True).get("Item")
        return PaymentSession.from_item(item) if item else None

    def _first_active(self, items: list[dict], now: float) -> PaymentSession | None:
        active = [
            i for i in items
            if SessionStatus(i["status"]) in ACTIVE_STATUSES and float(i["expires_at"]) > now
        ]
        if not active:
            return None
        # GSI reads are eventually consistent: re-read the winner from the base table
        best = max(active, key=lambda i: float(i["created_at"]))
        return self.get(best["session_id"])

    def find_active_by_code(self, code: str, now: float) -> PaymentSession | None:
        from boto3.dynamodb.conditions import Key

        resp = self._table.query(IndexName="payment_code-index", KeyConditionExpression=Key("payment_code").eq(code))
        return self._first_active(resp.get("Items", []), now)

    def find_active_by_ani(self, ani: str, now: float) -> PaymentSession | None:
        from boto3.dynamodb.conditions import Key

        resp = self._table.query(
            IndexName="caller_ani-index",
            KeyConditionExpression=Key("caller_ani").eq(ani) & Key("created_at").gt(self._to_ddb(now - 3600)),
            ScanIndexForward=False,
            Limit=5,
        )
        return self._first_active(resp.get("Items", []), now)

    def code_in_use(self, code: str, now: float) -> bool:
        return self.find_active_by_code(code, now) is not None

    def transition(self, session_id, allowed_from, updates):
        from botocore.exceptions import ClientError

        names, values, sets = {"#status": "status"}, {}, []
        for i, (k, v) in enumerate(updates.items()):
            names[f"#f{i}"] = k
            values[f":v{i}"] = self._to_ddb(v.value if isinstance(v, SessionStatus) else v)
            sets.append(f"#f{i} = :v{i}")
        allowed = list(allowed_from)
        for j, s in enumerate(allowed):
            values[f":s{j}"] = s.value
        condition = "#status IN (" + ", ".join(f":s{j}" for j in range(len(allowed))) + ")"
        try:
            resp = self._table.update_item(
                Key={"session_id": session_id},
                UpdateExpression="SET " + ", ".join(sets),
                ConditionExpression=condition,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
        return PaymentSession.from_item(resp["Attributes"])

    def list_recent(self, limit: int = 100) -> list[PaymentSession]:
        # POC only: a Scan is fine at 311 volumes. Production reporting reads the
        # S3 archive via Athena/QuickSight instead.
        items = self._table.scan(Limit=1000).get("Items", [])
        items.sort(key=lambda i: float(i["created_at"]), reverse=True)
        return [PaymentSession.from_item(i) for i in items[:limit]]


_cached: SessionRepository | None = None


def build_repository(settings, fresh: bool = False) -> SessionRepository:
    global _cached
    if _cached is not None and not fresh:
        return _cached
    repo: SessionRepository
    if settings.repository == "dynamodb":
        repo = DynamoSessionRepository(settings.table_name)
    else:
        repo = InMemorySessionRepository()
    if not fresh:
        _cached = repo
    return repo


def now() -> float:
    return time.time()

import json
import sys
import types

from conftest import ANI, APPROVE, DECLINE

from handlers import agent_api, connect_payment, stream_archiver


def http(route, body=None, **path):
    method = route.split(" ")[0]
    return agent_api.handler({
        "routeKey": route, "pathParameters": path or None,
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {"http": {"method": method},
                           "authorizer": {"jwt": {"claims": {"email": "agent@wilm.gov"}}}},
    })


def create(**extra):
    r = http("POST /sessions", {"serviceRequestId": "SR-77", "callerAni": ANI,
                                "items": [{"code": "MATTRESS", "quantity": 1}], **extra})
    assert r["statusCode"] == 201, r
    return json.loads(r["body"])


def connect(params, ani=ANI):
    return connect_payment.handler({"Details": {
        "ContactData": {"ContactId": "c-1", "CustomerEndpoint": {"Address": ani, "Type": "TELEPHONE_NUMBER"}},
        "Parameters": params}, "Name": "ContactFlowEvent"})


def test_agent_api_lifecycle():
    assert len(json.loads(http("GET /fees")["body"])["items"]) >= 1
    body = create(telephonySessionId="s-1", partyId="p-1")
    s = body["session"]
    assert s["amountCents"] == 2000 and s["status"] == "PENDING"
    assert body["transfer"] == {"dryRun": True, "target": "+13025550199"}
    got = json.loads(http("GET /sessions/{id}", id=s["sessionId"])["body"])["session"]
    assert got["sessionId"] == s["sessionId"]
    assert http("POST /sessions/{id}/cancel", id=s["sessionId"])["statusCode"] == 200
    assert http("POST /sessions/{id}/cancel", id=s["sessionId"])["statusCode"] == 409


def test_agent_api_errors():
    assert http("GET /sessions/{id}", id="nope")["statusCode"] == 404
    assert http("POST /sessions", {"serviceRequestId": "SR-1", "items": []})["statusCode"] == 422
    bad = agent_api.handler({"routeKey": "POST /sessions", "body": "{not json",
                             "requestContext": {"http": {"method": "POST"}}})
    assert bad["statusCode"] == 400


def test_connect_lookup_and_charge():
    sid = create()["session"]["sessionId"]
    found = connect({"action": "lookup"})
    assert found == {"found": "true", "sessionId": sid, "amountCents": "2000",
                     "amountSpoken": "20 dollars", "serviceRequestId": "SR-77"}
    assert all(isinstance(v, str) for v in found.values())     # Connect needs flat strings

    declined = connect({"action": "charge", "sessionId": sid, "encCardNumber": DECLINE,
                        "encExpiry": "1240", "encCvv": "123", "postalCode": "19801"})
    assert declined["result"] == "RETRY"
    ok = connect({"action": "charge", "sessionId": sid, "encCardNumber": APPROVE,
                  "encExpiry": "1240", "encCvv": "123", "postalCode": "19801"})
    assert ok["result"] == "APPROVED" and ok["last4"] == "1111"


def test_connect_lookup_by_code_and_invalid_card():
    s = create()["session"]
    assert connect({"action": "lookup", "paymentCode": s["paymentCode"]}, ani="+19995550000")["found"] == "true"
    bad = connect({"action": "charge", "sessionId": s["sessionId"], "encCardNumber": "1234",
                   "encExpiry": "1240", "encCvv": "123"})
    assert bad["result"] == "INVALID_CARD"
    assert connect({"action": "lookup"}, ani="+19995550000") == {"found": "false"}


def test_connect_handler_never_logs_card(capsys):
    sid = create()["session"]["sessionId"]
    connect({"action": "charge", "sessionId": sid, "encCardNumber": APPROVE,
             "encExpiry": "1240", "encCvv": "987"})
    logs = capsys.readouterr()
    out = logs.out + logs.err
    assert APPROVE not in out and '"encCvv": "***"' in out


def test_stream_archiver_writes_only_terminal(monkeypatch):
    puts = []
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda name: types.SimpleNamespace(put_object=lambda **kw: puts.append(kw))
    fake_types = types.ModuleType("boto3.dynamodb.types")

    class Deser:
        def deserialize(self, v):
            return v.get("S") if "S" in v else float(v["N"])

    fake_types.TypeDeserializer = Deser
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "boto3.dynamodb", types.ModuleType("boto3.dynamodb"))
    monkeypatch.setitem(sys.modules, "boto3.dynamodb.types", fake_types)
    monkeypatch.setenv("ARCHIVE_BUCKET", "bucket")

    def rec(status):
        return {"eventName": "MODIFY", "dynamodb": {"NewImage": {
            "session_id": {"S": f"id-{status}"}, "status": {"S": status},
            "created_at": {"N": "1790000000"}, "completed_at": {"N": "1790000100"}}}}

    out = stream_archiver.handler({"Records": [rec("PROCESSING"), rec("APPROVED")]})
    assert out == {"archived": 1}
    assert puts[0]["Key"].startswith("payments/dt=2026-") and puts[0]["Key"].endswith("id-APPROVED.json")

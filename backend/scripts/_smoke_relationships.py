"""Smoke-test the relationships API against the productive DB."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["REPOLENS_DB_PATH"] = os.path.join(os.getcwd(), "data", "repolens.db")

from fastapi.testclient import TestClient

from app.main import create_app

with TestClient(create_app()) as client:
    r = client.get("/api/repositories/1/relationships?limit=3")
    print("itsdangerous relationships:", r.status_code)
    body = r.json()
    print("  keys:", sorted(body.keys()))
    for item in body.get("relationships", [])[:3]:
        print("  ", item["type"], item["resolution_status"], "|", item["evidence"])

    r2 = client.get("/api/repositories/1/relationships?type=CALLS&status=RESOLVED")
    b2 = r2.json()
    print("filtered CALLS/RESOLVED total:", b2.get("total"))

    r3 = client.post("/api/repositories/2/relationships")
    print("flask rebuild:", r3.status_code, r3.json())

    r4 = client.get("/api/repositories/1/symbols/13/relationships")
    b4 = r4.json()
    print("BadData neighborhood:", r4.status_code, {k: len(v) for k, v in b4.items()})
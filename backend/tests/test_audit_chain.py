import sqlite3

import pytest

from app.db import Database
from app.audit import audit


@pytest.fixture
def heads(client):
    out = {}
    for user, pw in (("viewer", "viewer-dev"), ("operator", "test-operator-pw"), ("admin", "admin-dev")):
        r = client.post("/api/auth/login", json={"username": user, "password": pw})
        assert r.status_code == 200, r.text
        out[user] = {"Authorization": f"Bearer {r.json()['token']}"}
    return out


def verify(client, h):
    r = client.get("/api/operator/audit/verify", headers=h)
    assert r.status_code == 200
    return r.json()


def fill(ctx, n=5):
    for i in range(n):
        audit(ctx.db, "operator", "operator", f"act{i}", str(i), {"i": i})


def test_untouched_chain_verifies(client, ctx, heads):
    fill(ctx)
    res = verify(client, heads["operator"])
    assert res["ok"] is True and res["rows"] >= 5 and res["broken_at"] is None and len(res["head"]) == 16


def test_verify_is_operator_only_and_audited(client, ctx, heads):
    assert client.get("/api/operator/audit/verify").status_code == 401
    assert client.get("/api/operator/audit/verify", headers=heads["viewer"]).status_code == 403
    assert verify(client, heads["admin"])["ok"] is True
    assert ctx.db.query("SELECT 1 FROM audit_log WHERE action='audit_verify' AND result='ok'")


def test_editing_a_row_breaks_verification_at_that_row(client, ctx, heads):
    fill(ctx)
    target = ctx.db.query("SELECT id FROM audit_log WHERE action='act2'")[0]["id"]
    ctx.db.execute("UPDATE audit_log SET result='denied' WHERE id=?", (target,))
    res = verify(client, heads["operator"])
    assert res["ok"] is False and res["broken_at"] == target and res["reason"] == "row_modified"


def test_deleting_a_middle_row_is_detected(client, ctx, heads):
    fill(ctx)
    rows = ctx.db.query("SELECT id FROM audit_log WHERE action='act2'")
    ctx.db.execute("DELETE FROM audit_log WHERE id=?", (rows[0]["id"],))
    res = verify(client, heads["operator"])
    assert res["ok"] is False and res["reason"] == "broken_link" and res["broken_at"] > rows[0]["id"]


def test_deleting_the_newest_row_is_detected(client, ctx, heads):
    fill(ctx)
    last = ctx.db.query("SELECT MAX(id) AS m FROM audit_log")[0]["m"]
    ctx.db.execute("DELETE FROM audit_log WHERE id=?", (last,))
    res = verify(client, heads["operator"])
    assert res["ok"] is False and res["reason"] == "tail_missing"


def test_a_forged_row_without_a_hash_is_detected(client, ctx, heads):
    fill(ctx)
    ctx.db.execute("INSERT INTO audit_log(ts,actor,role,action,result) VALUES('t','x','operator','forged','ok')")
    res = verify(client, heads["operator"])
    assert res["ok"] is False and res["reason"] == "row_without_hash"


def test_audit_read_endpoint_does_not_expose_hashes(client, ctx, heads):
    fill(ctx, 2)
    rows = client.get("/api/operator/audit?limit=5", headers=heads["viewer"]).json()
    assert rows and all("hash" not in r and "prev_hash" not in r for r in rows)


def test_old_database_is_migrated_and_its_rows_get_chained(tmp_path):
    path = str(tmp_path / "old.db")
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, role TEXT NOT NULL, "
              "action TEXT NOT NULL, target TEXT, params TEXT, result TEXT NOT NULL)")
    for i in range(3):
        c.execute("INSERT INTO audit_log(ts,actor,role,action,result) VALUES('t','a','operator',?, 'ok')", (f"old{i}",))
    c.commit()
    c.close()
    db = Database(path)
    assert db.verify_audit_chain()["ok"] is True and db.verify_audit_chain()["rows"] == 3
    audit(db, "a", "operator", "new")
    assert db.verify_audit_chain() == {**db.verify_audit_chain(), "ok": True, "rows": 4}
    db.execute("UPDATE audit_log SET actor='evil' WHERE action='old1'")
    assert db.verify_audit_chain()["ok"] is False
    db2 = Database(path)                       # re-opening must not re-chain (and so hide) the tampering
    assert db2.verify_audit_chain()["ok"] is False


def test_concurrent_writers_keep_one_unbroken_chain(ctx):
    import threading
    ts = [threading.Thread(target=lambda: [audit(ctx.db, "t", "operator", "c") for _ in range(30)]) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert ctx.db.verify_audit_chain()["ok"] is True

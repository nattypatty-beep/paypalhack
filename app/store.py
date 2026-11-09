"""SQLite persistence layer.

The database stores JSON documents in a handful of tables.  It is intentionally
small and dependency-free; the whole demo runs on a single SQLite file you can
inspect with any SQLite browser.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .util import canonical_json, dumps, loads, new_id, now_iso, sha256_text

GENESIS_HASH = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS mandates (
    id TEXT PRIMARY KEY,
    principal TEXT NOT NULL,
    currency TEXT NOT NULL,
    budget_cents INTEGER NOT NULL,
    approval_threshold_cents INTEGER NOT NULL,
    per_txn_limit_cents INTEGER,
    daily_limit_cents INTEGER,
    categories TEXT NOT NULL,
    status TEXT NOT NULL,
    raw_text TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    mandate_id TEXT NOT NULL,
    title TEXT NOT NULL,
    brief TEXT DEFAULT '',
    category TEXT NOT NULL,
    currency TEXT NOT NULL,
    total_cents INTEGER NOT NULL,
    status TEXT NOT NULL,
    freelancer_name TEXT DEFAULT '',
    freelancer_email TEXT DEFAULT '',
    paypal_order_id TEXT,
    paypal_auth_id TEXT,
    blocked_reason TEXT,
    meta TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS milestones (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    criteria TEXT NOT NULL,
    status TEXT NOT NULL,
    due_at TEXT,
    submission_id TEXT,
    capture_id TEXT,
    payout_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
    id TEXT PRIMARY KEY,
    milestone_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    notes TEXT DEFAULT '',
    deliverables TEXT NOT NULL,
    status TEXT NOT NULL,
    verdict TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    mandate_id TEXT NOT NULL,
    job_id TEXT,
    milestone_id TEXT,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL,
    reasons TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    resolved_at TEXT,
    resolver TEXT,
    note TEXT DEFAULT '',
    token TEXT,
    token_expires_at TEXT,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    job_id TEXT,
    milestone_id TEXT,
    paypal_id TEXT,
    related_id TEXT,
    amount_cents INTEGER NOT NULL,
    fee_cents INTEGER DEFAULT 0,
    currency TEXT NOT NULL,
    description TEXT DEFAULT '',
    receipt TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY,
    id TEXT UNIQUE NOT NULL,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    subject_type TEXT DEFAULT '',
    subject_id TEXT DEFAULT '',
    decision TEXT DEFAULT '',
    amount_cents INTEGER,
    currency TEXT DEFAULT '',
    reason_codes TEXT DEFAULT '[]',
    message TEXT DEFAULT '',
    meta TEXT DEFAULT '{}',
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paypal_objects (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    amount_cents INTEGER DEFAULT 0,
    currency TEXT DEFAULT 'USD',
    meta TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    mime TEXT DEFAULT '',
    size INTEGER DEFAULT 0,
    sha256 TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_jobs_mandate ON jobs(mandate_id);
CREATE INDEX IF NOT EXISTS ix_milestones_job ON milestones(job_id);
CREATE INDEX IF NOT EXISTS ix_submissions_milestone ON submissions(milestone_id);
CREATE INDEX IF NOT EXISTS ix_payments_job ON payments(job_id);
CREATE INDEX IF NOT EXISTS ix_audit_decision ON audit(decision);
"""

JSON_COLUMNS: dict[str, set[str]] = {
    "mandates": {"categories"},
    "jobs": {"meta"},
    "milestones": {"criteria"},
    "submissions": {"deliverables", "verdict"},
    "approvals": {"reasons", "snapshot"},
    "payments": {"receipt"},
    "audit": {"reason_codes", "meta"},
    "paypal_objects": {"meta"},
    "uploads": set(),
}

TABLES_WITH_UPDATED_AT = {"jobs", "milestones", "payments", "paypal_objects"}


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=OFF")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    @contextmanager
    def tx(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            row = cur.fetchone()
            return dict(row) if row else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def vacuum(self) -> None:
        with self._lock:
            self._conn.execute("VACUUM")


def _encode(table: str, values: dict[str, Any]) -> dict[str, Any]:
    encoded = dict(values)
    for key in JSON_COLUMNS.get(table, set()):
        if key in encoded and not isinstance(encoded[key], str):
            encoded[key] = dumps(encoded[key])
    return encoded


def _decode(table: str, row: dict | None) -> dict | None:
    if row is None:
        return None
    decoded = dict(row)
    for key in JSON_COLUMNS.get(table, set()):
        if key in decoded:
            default = [] if key in {"categories", "criteria", "deliverables", "reasons", "reason_codes"} else {}
            decoded[key] = loads(decoded[key], default)
    return decoded


class Store:
    """Domain repository on top of :class:`Database`."""

    def __init__(self, db: Database):
        self.db = db

    # -- generic ----------------------------------------------------------
    def _insert(self, table: str, values: dict[str, Any]) -> dict:
        payload = _encode(table, values)
        columns = ", ".join(payload)
        placeholders = ", ".join("?" for _ in payload)
        with self.db.tx() as conn:
            conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(payload.values()))
        return payload

    def update(self, table: str, row_id: str, values: dict[str, Any]) -> None:
        if not values:
            return
        payload = _encode(table, values)
        if table in TABLES_WITH_UPDATED_AT and "updated_at" not in payload:
            payload["updated_at"] = now_iso()
        assignments = ", ".join(f"{col} = ?" for col in payload)
        with self.db.tx() as conn:
            conn.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", (*payload.values(), row_id))

    # -- mandates ---------------------------------------------------------
    def insert_mandate(self, values: dict) -> dict:
        return _decode("mandates", self._insert("mandates", values))  # type: ignore[return-value]

    def get_mandate(self, mandate_id: str) -> dict | None:
        return _decode(
            "mandates",
            self.db.one("SELECT * FROM mandates WHERE id = ?", (mandate_id,)),
        )

    def list_mandates(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM mandates ORDER BY created_at DESC")
        return [_decode("mandates", row) for row in rows]  # type: ignore[list-item]

    def update_mandate(self, mandate_id: str, values: dict) -> None:
        self.update("mandates", mandate_id, values)

    # -- jobs -------------------------------------------------------------
    def insert_job(self, values: dict) -> dict:
        return _decode("jobs", self._insert("jobs", values))  # type: ignore[return-value]

    def insert_milestone(self, values: dict) -> dict:
        return _decode("milestones", self._insert("milestones", values))  # type: ignore[return-value]

    def get_job(self, job_id: str, with_milestones: bool = True) -> dict | None:
        job = _decode("jobs", self.db.one("SELECT * FROM jobs WHERE id = ?", (job_id,)))
        if job is None:
            return None
        if with_milestones:
            job["milestones"] = self.list_milestones(job_id)
        return job

    def list_jobs(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM jobs ORDER BY created_at DESC")
        jobs = [_decode("jobs", row) for row in rows]
        for job in jobs:
            if job is not None:
                job["milestones"] = self.list_milestones(job["id"])
        return [j for j in jobs if j is not None]

    def update_job(self, job_id: str, values: dict) -> None:
        self.update("jobs", job_id, values)

    def list_milestones(self, job_id: str) -> list[dict]:
        rows = self.db.query("SELECT * FROM milestones WHERE job_id = ? ORDER BY idx ASC", (job_id,))
        milestones = [_decode("milestones", row) for row in rows]
        result = []
        for milestone in milestones:
            if milestone is None:
                continue
            milestone["latest_submission"] = self.latest_submission(milestone["id"])
            result.append(milestone)
        return result

    def get_milestone(self, milestone_id: str) -> dict | None:
        milestone = _decode(
            "milestones",
            self.db.one("SELECT * FROM milestones WHERE id = ?", (milestone_id,)),
        )
        if milestone:
            milestone["latest_submission"] = self.latest_submission(milestone_id)
        return milestone

    def update_milestone(self, milestone_id: str, values: dict) -> None:
        self.update("milestones", milestone_id, values)

    # -- submissions ------------------------------------------------------
    def insert_submission(self, values: dict) -> dict:
        return _decode("submissions", self._insert("submissions", values))  # type: ignore[return-value]

    def get_submission(self, submission_id: str) -> dict | None:
        return _decode(
            "submissions",
            self.db.one("SELECT * FROM submissions WHERE id = ?", (submission_id,)),
        )

    def latest_submission(self, milestone_id: str) -> dict | None:
        return _decode(
            "submissions",
            self.db.one(
                "SELECT * FROM submissions WHERE milestone_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (milestone_id,),
            ),
        )

    def update_submission(self, submission_id: str, values: dict) -> None:
        self.update("submissions", submission_id, values)

    # -- approvals --------------------------------------------------------
    def insert_approval(self, values: dict) -> dict:
        return _decode("approvals", self._insert("approvals", values))  # type: ignore[return-value]

    def get_approval(self, approval_id: str) -> dict | None:
        return _decode(
            "approvals",
            self.db.one("SELECT * FROM approvals WHERE id = ?", (approval_id,)),
        )

    def list_approvals(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self.db.query(
                "SELECT * FROM approvals WHERE status = ? ORDER BY requested_at DESC", (status,)
            )
        else:
            rows = self.db.query("SELECT * FROM approvals ORDER BY requested_at DESC")
        return [_decode("approvals", row) for row in rows]  # type: ignore[list-item]

    def update_approval(self, approval_id: str, values: dict) -> None:
        self.update("approvals", approval_id, values)

    def find_pending_approval(self, mandate_id: str, job_id: str | None, amount_cents: int) -> dict | None:
        return _decode(
            "approvals",
            self.db.one(
                """SELECT * FROM approvals
                   WHERE mandate_id = ? AND status = 'pending'
                     AND COALESCE(job_id, '') = COALESCE(?, '')
                     AND amount_cents = ?
                   ORDER BY requested_at DESC LIMIT 1""",
                (mandate_id, job_id, amount_cents),
            ),
        )

    def expire_stale_approvals(self) -> int:
        now = now_iso()
        with self.db.tx() as conn:
            cur = conn.execute(
                "UPDATE approvals SET status = 'expired', resolved_at = ? WHERE status = 'pending' AND expires_at <= ?",
                (now, now),
            )
            return cur.rowcount

    # -- payments ---------------------------------------------------------
    def insert_payment(self, values: dict) -> dict:
        return _decode("payments", self._insert("payments", values))  # type: ignore[return-value]

    def get_payment(self, payment_id: str) -> dict | None:
        return _decode("payments", self.db.one("SELECT * FROM payments WHERE id = ?", (payment_id,)))

    def update_payment(self, payment_id: str, values: dict) -> None:
        self.update("payments", payment_id, values)

    def list_payments(self, limit: int = 300) -> list[dict]:
        rows = self.db.query("SELECT * FROM payments ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))
        return [_decode("payments", row) for row in rows]  # type: ignore[list-item]

    def job_authorization(self, job_id: str) -> dict | None:
        return _decode(
            "payments",
            self.db.one(
                """SELECT * FROM payments
                   WHERE job_id = ? AND kind = 'authorization'
                     AND status IN ('authorized', 'partially_captured')
                   ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (job_id,),
            ),
        )

    def job_financials(self, job_id: str) -> dict:
        auth = self.job_authorization(job_id)
        captured = self.db.one(
            """SELECT COALESCE(SUM(amount_cents), 0) AS total FROM payments
               WHERE job_id = ? AND kind = 'capture' AND status = 'captured'""",
            (job_id,),
        )
        captured_cents = int((captured or {}).get("total") or 0)
        authorized = int(auth["amount_cents"]) if auth else 0
        return {
            "authorization_id": auth["id"] if auth else None,
            "paypal_auth_id": auth["paypal_id"] if auth else None,
            "authorized_cents": authorized,
            "captured_cents": captured_cents,
            "remaining_cents": max(0, authorized - captured_cents),
            "authorization": auth,
        }

    def mandate_financials(self, mandate_id: str) -> dict:
        mandate = self.get_mandate(mandate_id)
        if not mandate:
            return {
                "budget_cents": 0,
                "spent_cents": 0,
                "committed_cents": 0,
                "pending_cents": 0,
                "available_cents": 0,
                "authorized_cents": 0,
                "released_cents": 0,
            }
        captured = self.db.query(
            """SELECT p.job_id, COALESCE(SUM(p.amount_cents), 0) AS total
               FROM payments p JOIN jobs j ON j.id = p.job_id
               WHERE j.mandate_id = ? AND p.kind = 'capture' AND p.status = 'captured'
               GROUP BY p.job_id""",
            (mandate_id,),
        )
        captured_by_job = {row["job_id"]: int(row["total"]) for row in captured}
        authorizations = self.db.query(
            """SELECT p.job_id, p.amount_cents FROM payments p JOIN jobs j ON j.id = p.job_id
               WHERE j.mandate_id = ? AND p.kind = 'authorization'
                 AND p.status IN ('authorized', 'partially_captured')""",
            (mandate_id,),
        )
        committed = 0
        authorized_total = 0
        for auth in authorizations:
            amount = int(auth["amount_cents"])
            authorized_total += amount
            committed += max(0, amount - captured_by_job.get(auth["job_id"], 0))
        spent = sum(captured_by_job.values())
        pending_row = self.db.one(
            """SELECT COALESCE(SUM(amount_cents), 0) AS total FROM approvals
               WHERE mandate_id = ? AND status = 'pending' AND expires_at > ?""",
            (mandate_id, now_iso()),
        )
        pending = int((pending_row or {}).get("total") or 0)
        payouts = self.db.one(
            """SELECT COALESCE(SUM(p.amount_cents), 0) AS total FROM payments p JOIN jobs j ON j.id = p.job_id
               WHERE j.mandate_id = ? AND p.kind = 'payout' AND p.status = 'completed'""",
            (mandate_id,),
        )
        budget = int(mandate["budget_cents"])
        return {
            "budget_cents": budget,
            "spent_cents": spent,
            "committed_cents": committed,
            "pending_cents": pending,
            "available_cents": max(0, budget - spent - committed - pending),
            "authorized_cents": authorized_total,
            "released_cents": spent,
            "payouts_cents": int((payouts or {}).get("total") or 0),
        }

    def recent_authorized_cents(self, mandate_id: str, hours: int = 24) -> int:
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds").replace("+00:00", "Z")
        row = self.db.one(
            """SELECT COALESCE(SUM(p.amount_cents), 0) AS total FROM payments p JOIN jobs j ON j.id = p.job_id
               WHERE j.mandate_id = ? AND p.kind = 'authorization' AND p.created_at >= ?""",
            (mandate_id, cutoff),
        )
        return int((row or {}).get("total") or 0)

    # -- audit ------------------------------------------------------------
    def append_audit(
        self,
        actor: str,
        action: str,
        *,
        subject_type: str = "",
        subject_id: str = "",
        decision: str = "",
        amount_cents: int | None = None,
        currency: str = "",
        reason_codes: list[str] | None = None,
        message: str = "",
        meta: dict | None = None,
        ts: str | None = None,
    ) -> dict:
        entry_id = new_id("aud")
        entry_ts = ts or now_iso()
        reasons = reason_codes or []
        payload_meta = meta or {}
        with self.db.tx() as conn:
            last = conn.execute("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            seq = (int(last["seq"]) + 1) if last else 1
            prev_hash = str(last["hash"]) if last else GENESIS_HASH
            body = {
                "seq": seq,
                "id": entry_id,
                "ts": entry_ts,
                "actor": actor,
                "action": action,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "decision": decision,
                "amount_cents": amount_cents,
                "currency": currency,
                "reason_codes": reasons,
                "message": message,
                "meta": payload_meta,
                "prev_hash": prev_hash,
            }
            digest = sha256_text(canonical_json(body))
            conn.execute(
                """INSERT INTO audit (seq, id, ts, actor, action, subject_type, subject_id, decision,
                                      amount_cents, currency, reason_codes, message, meta, prev_hash, hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    seq,
                    entry_id,
                    entry_ts,
                    actor,
                    action,
                    subject_type,
                    subject_id,
                    decision,
                    amount_cents,
                    currency,
                    dumps(reasons),
                    message,
                    dumps(payload_meta),
                    prev_hash,
                    digest,
                ),
            )
        return _decode(
            "audit",
            {
                "seq": seq,
                "id": entry_id,
                "ts": entry_ts,
                "actor": actor,
                "action": action,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "decision": decision,
                "amount_cents": amount_cents,
                "currency": currency,
                "reason_codes": dumps(reasons),
                "message": message,
                "meta": dumps(payload_meta),
                "prev_hash": prev_hash,
                "hash": digest,
            },
        )  # type: ignore[return-value]

    def list_audit(self, limit: int = 300, after_seq: int = 0) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM audit WHERE seq > ? ORDER BY seq DESC LIMIT ?", (after_seq, limit)
        )
        return [_decode("audit", row) for row in rows]  # type: ignore[list-item]

    def audit_count(self) -> int:
        row = self.db.one("SELECT COUNT(*) AS n FROM audit")
        return int((row or {}).get("n") or 0)

    def count_blocked(self) -> int:
        row = self.db.one("SELECT COUNT(*) AS n FROM audit WHERE decision = 'block'")
        return int((row or {}).get("n") or 0)

    def audit_verify(self) -> dict:
        rows = self.db.query("SELECT * FROM audit ORDER BY seq ASC")
        prev_hash = GENESIS_HASH
        for row in rows:
            if row["prev_hash"] != prev_hash:
                return {"ok": False, "count": len(rows), "broken_at": row["seq"], "reason": "prev_hash mismatch"}
            body = {
                "seq": row["seq"],
                "id": row["id"],
                "ts": row["ts"],
                "actor": row["actor"],
                "action": row["action"],
                "subject_type": row["subject_type"],
                "subject_id": row["subject_id"],
                "decision": row["decision"],
                "amount_cents": row["amount_cents"],
                "currency": row["currency"],
                "reason_codes": loads(row["reason_codes"], []),
                "message": row["message"],
                "meta": loads(row["meta"], {}),
                "prev_hash": row["prev_hash"],
            }
            digest = sha256_text(canonical_json(body))
            if digest != row["hash"]:
                return {"ok": False, "count": len(rows), "broken_at": row["seq"], "reason": "hash mismatch"}
            prev_hash = row["hash"]
        return {
            "ok": True,
            "count": len(rows),
            "broken_at": None,
            "head": prev_hash,
        }

    def audit_tamper(self, seq: int | None = None) -> dict | None:
        """Deliberately corrupt a row (demo only) so the chain check can catch it."""
        with self.db.tx() as conn:
            if seq is None:
                row = conn.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            else:
                row = conn.execute("SELECT * FROM audit WHERE seq = ?", (seq,)).fetchone()
            if not row:
                return None
            new_message = f"[tampered] {row['message']}"
            conn.execute("UPDATE audit SET message = ? WHERE seq = ?", (new_message, row["seq"]))
            result = dict(row)
            result["message"] = new_message
            return result

    # -- uploads ----------------------------------------------------------
    def insert_upload(self, values: dict) -> dict:
        return self._insert("uploads", values)

    def get_upload(self, upload_id: str) -> dict | None:
        return _decode("uploads", self.db.one("SELECT * FROM uploads WHERE id = ?", (upload_id,)))

    # -- mock PayPal objects ---------------------------------------------
    def put_paypal_object(self, values: dict) -> dict:
        return _decode("paypal_objects", self._insert("paypal_objects", values))  # type: ignore[return-value]

    def get_paypal_object(self, object_id: str) -> dict | None:
        return _decode(
            "paypal_objects",
            self.db.one("SELECT * FROM paypal_objects WHERE id = ?", (object_id,)),
        )

    def update_paypal_object(self, object_id: str, values: dict) -> None:
        self.update("paypal_objects", object_id, values)

    # -- demo reset -------------------------------------------------------
    def reset(self) -> None:
        with self.db.tx() as conn:
            for table in [
                "audit",
                "payments",
                "approvals",
                "submissions",
                "milestones",
                "jobs",
                "mandates",
                "paypal_objects",
                "uploads",
            ]:
                conn.execute(f"DELETE FROM {table}")
        self.db.vacuum()

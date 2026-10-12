"""SQLite transactions separate editable content from immutable trial content."""
from __future__ import annotations
import json
import sqlite3
import uuid
import time
from contextlib import contextmanager
from pathlib import Path
from model import (FORMAT, SAVE_FORMAT, LEGACY_FORMAT, OLD_FORMATS, upgrade_state, WorldError, apply_action, canonical, digest,
                   fail, ident, initial_state, integer, validate_package, validate_save)


class Store:
    def __init__(self, path, example):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        p, _ = validate_package(example)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS draft (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, package TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS trial (id TEXT PRIMARY KEY, package TEXT NOT NULL, state TEXT NOT NULL, created INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS receipt (trial TEXT NOT NULL, execution TEXT NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(trial, execution));
                CREATE TABLE IF NOT EXISTS editor_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS recovery (client TEXT PRIMARY KEY, sequence INTEGER NOT NULL, package TEXT, base_revision INTEGER NOT NULL, base_fingerprint TEXT NOT NULL, updated INTEGER NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO draft VALUES (1, 0, ?)", (canonical(p),))
            db.execute("INSERT OR IGNORE INTO editor_meta VALUES ('workspace_id', ?)", (uuid.uuid4().hex,))
            # Upgrade content, never rewind runtime state or action receipts.
            revision, old = db.execute("SELECT revision,package FROM draft WHERE id=1").fetchone()
            old = json.loads(old)
            if old.get("format") in OLD_FORMATS:
                upgraded, _ = validate_package(old)
                db.execute("UPDATE draft SET package=?,revision=? WHERE id=1", (canonical(upgraded), revision + 1))
            for tid, package, state in db.execute("SELECT id,package,state FROM trial").fetchall():
                old = json.loads(package)
                if old.get("format") in OLD_FORMATS:
                    upgraded, _ = validate_package(old)
                    migrated=upgrade_state(upgraded,json.loads(state))
                    db.execute("UPDATE trial SET package=?,state=? WHERE id=?", (canonical(upgraded),canonical(migrated),tid))

    def workspace_id(self):
        with self.connection() as db:
            return db.execute("SELECT value FROM editor_meta WHERE key='workspace_id'").fetchone()[0]

    def recovery_write(self, data):
        client = ident(data.get("client_id"), "恢复副本 ID")
        sequence = integer(data.get("sequence"), "恢复序号", 1, 10000000)
        revision = integer(data.get("base_revision"), "恢复基础版本", 0, 10000000)
        base = data.get("base_fingerprint")
        if not isinstance(base, str) or len(base) > 8000000:
            fail("恢复副本基础内容无效。")
        package = data.get("package")
        if package is not None and (not isinstance(package, dict) or package.get("format") not in (FORMAT, *OLD_FORMATS)):
            fail("恢复副本需要是世界草稿。")
        payload = canonical(package) if package is not None else None
        # Unfinished property fields are intentionally kept, not accepted as a
        # validated world. The UI shape-checks recovery and formal save validates.
        now = int(time.time() * 1000)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO recovery VALUES (?,?,?,?,?,?) ON CONFLICT(client) DO UPDATE SET sequence=excluded.sequence,package=excluded.package,base_revision=excluded.base_revision,base_fingerprint=excluded.base_fingerprint,updated=excluded.updated WHERE excluded.sequence>recovery.sequence", (client, sequence, payload, revision, base, now))
        return {"saved": True, "sequence": sequence}

    def recoveries(self):
        with self.connection() as db:
            rows = db.execute("SELECT client,package,base_revision,base_fingerprint,updated FROM recovery WHERE package IS NOT NULL ORDER BY updated DESC").fetchall()
        return [{"schema": 1, "client_id": client, "package": json.loads(p), "baseRevision": revision, "baseFingerprint": base, "updated": updated} for client, p, revision, base, updated in rows]

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def draft(self):
        with self.connection() as db:
            revision, package = db.execute("SELECT revision,package FROM draft WHERE id=1").fetchone()
        return {"revision": revision, "package": json.loads(package)}

    def save_draft(self, package, expected):
        p, warnings = validate_package(package)
        integer(expected, "预期草稿版本", 0, 10000000)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("UPDATE draft SET revision=revision+1, package=? WHERE id=1 AND revision=?", (canonical(p), expected))
            if result.rowcount != 1:
                fail("草稿已在其他窗口改变。请先下载当前内容，再刷新以载入最新草稿。", "STALE", 409)
        return {"revision": expected + 1, "package": p, "warnings": warnings}

    def create_trial(self, package=None, save=None):
        if save is not None:
            p, state = validate_save(save)
        else:
            p, _ = validate_package(package)
            state = initial_state(p)
        tid = uuid.uuid4().hex
        with self.connection() as db:
            db.execute("INSERT INTO trial VALUES (?, ?, ?, CAST(strftime('%s','now') AS INTEGER))", (tid, canonical(p), canonical(state)))
        return {"id": tid, "package": p, "package_hash": digest(p), "state": state}

    def trial(self, tid):
        ident(tid, "试运行 ID")
        with self.connection() as db:
            return self._trial(db, tid)

    def _trial(self, db, tid):
        row = db.execute("SELECT package,state FROM trial WHERE id=?", (tid,)).fetchone()
        if not row:
            fail("试运行不存在。请新建试运行或导入存档。", "NOT_FOUND", 404)
        p, state = map(json.loads, row)
        return {"id": tid, "package": p, "package_hash": digest(p), "state": state}

    def trials(self):
        with self.connection() as db:
            rows = db.execute("SELECT id,package,state FROM trial ORDER BY created DESC,rowid DESC LIMIT 50").fetchall()
        return [{"id": tid, "name": json.loads(p)["meta"]["name"], "revision": json.loads(s)["revision"]} for tid, p, s in rows]

    def execute(self, tid, execution, expected, command):
        ident(tid, "试运行 ID")
        ident(execution, "执行 ID")
        integer(expected, "预期试运行版本", 0, 10000000)
        request = canonical({"revision": expected, "command": command})
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT request,result FROM receipt WHERE trial=? AND execution=?", (tid, execution)).fetchone()
            if old:
                if old[0] != request:
                    fail("执行 ID 已用于另一条操作，不能重复使用。", "ID_CONFLICT", 409)
                # Return current state with the original receipt; replay never rewinds.
                return {**self._trial(db, tid), "event": json.loads(old[1]), "replayed": True}
            t = self._trial(db, tid)
            if t["state"]["revision"] != expected:
                fail("状态已改变，已阻止旧操作。请刷新试运行状态后重试。", "STALE", 409)
            new, event = apply_action(t["package"], t["state"], command)
            validate_save({"format": SAVE_FORMAT, "package": t["package"], "package_hash": t["package_hash"], "state": new})
            db.execute("UPDATE trial SET state=? WHERE id=?", (canonical(new), tid))
            db.execute("INSERT INTO receipt VALUES (?,?,?,?)", (tid, execution, request, canonical(event)))
            return {**t, "state": new, "event": event, "replayed": False}

    def export_save(self, tid):
        t = self.trial(tid)
        return {"format": SAVE_FORMAT, "package": t["package"], "package_hash": t["package_hash"], "state": t["state"]}

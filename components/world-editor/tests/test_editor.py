"""Acceptance tests exercise state, persistence, isolation and HTTP boundaries."""
import copy
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model import SAVE_FORMAT, WorldError, digest, validate_package, validate_save
from server import ROOT, create_server, parse_json
from store import Store


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = json.loads((ROOT / "examples/legacy-v1-bookstore.nyairo-world.json").read_text(encoding="utf-8"))
        self.db = Path(self.tmp.name) / "editor.db"
        self.store = Store(self.db, self.p)
        self.t = self.store.create_trial(self.p)
        self.serial = 0

    def tearDown(self):
        self.tmp.cleanup()

    def action(self, verb, target=None, **kwargs):
        self.serial += 1
        cmd = {"action": verb, **kwargs}
        if target:
            cmd["target"] = target
        self.t = self.store.execute(self.t["id"], "test-" + str(self.serial), self.t["state"]["revision"], cmd)
        return self.t

    def rejects_without_mutation(self, command, code):
        old = self.store.trial(self.t["id"])
        with self.assertRaises(WorldError) as error:
            self.store.execute(self.t["id"], "reject", old["state"]["revision"], command)
        self.assertEqual(error.exception.code, code)
        self.assertEqual(old, self.store.trial(self.t["id"]))

    def test_complete_read_move_rest_and_reload(self):
        self.rejects_without_mutation({"action": "read", "target": "old-book"}, "NOT_HELD")
        self.action("pick_up", "old-book")
        self.action("sit", "reading-chair")
        self.action("read", "old-book")
        self.assertEqual(self.t["state"]["objects"]["old-book"]["read_count"], 1)
        self.assertEqual(self.t["state"]["resident"]["pose"], "sitting")
        self.rejects_without_mutation({"action": "move", "target": "upstairs"}, "IN_USE")
        self.action("stand")
        self.action("move", "upstairs")
        self.assertEqual(self.t["state"]["objects"]["old-book"]["room_id"], "upstairs")
        self.action("place", "old-book")
        self.action("rest", "soft-bed")
        self.assertEqual(self.t["state"]["resident"]["energy"], 75)
        self.assertEqual(self.t["state"]["resident"]["using"], "soft-bed")
        restarted = Store(self.db, self.p)
        self.assertEqual(restarted.trial(self.t["id"]), self.store.trial(self.t["id"]))

    def test_container_storage_and_retrieval(self):
        self.action("pick_up", "old-book")
        self.action("store", "wood-cabinet")
        self.assertIsNone(self.t["state"]["resident"]["held"])
        self.assertEqual(self.t["state"]["objects"]["old-book"]["container_id"], "wood-cabinet")
        self.rejects_without_mutation({"action": "read", "target": "old-book"}, "STORED")
        self.action("retrieve", "wood-cabinet", item="old-book")
        self.assertEqual(self.t["state"]["resident"]["held"], "old-book")

    def test_capacity_and_held_exclusivity(self):
        self.p["objects"][3]["capacity"] = 1
        self.t = self.store.create_trial(self.p)
        self.action("pick_up", "old-book")
        self.rejects_without_mutation({"action": "pick_up", "target": "tea-cup"}, "OCCUPIED")
        self.action("store", "wood-cabinet")
        self.action("pick_up", "tea-cup")
        self.rejects_without_mutation({"action": "store", "target": "wood-cabinet"}, "OCCUPIED")

    def test_drink_consumes_actual_supply(self):
        self.action("pick_up", "tea-cup")
        self.action("drink", "tea-cup")
        self.action("drink", "tea-cup")
        self.assertEqual(self.t["state"]["objects"]["tea-cup"]["servings"], 0)
        self.assertEqual(self.t["state"]["resident"]["hydration"], 100)
        self.rejects_without_mutation({"action": "drink", "target": "tea-cup"}, "EMPTY")

    def test_unreachable_and_foreign_room(self):
        self.rejects_without_mutation({"action": "rest", "target": "soft-bed"}, "NOT_REACHABLE")
        self.p["connections"] = []
        self.t = self.store.create_trial(self.p)
        self.rejects_without_mutation({"action": "move", "target": "upstairs"}, "NOT_REACHABLE")
        _, warnings = validate_package(self.p)
        self.assertTrue(warnings)

    def test_open_hours_close_then_wait(self):
        self.p["initial"]["room_id"] = "upstairs"
        self.p["initial"]["time"] = "2026-10-11T07:55"
        self.t = self.store.create_trial(self.p)
        self.rejects_without_mutation({"action": "move", "target": "bookstore"}, "CLOSED")
        self.action("wait", minutes=5)
        self.action("move", "bookstore")
        self.assertEqual(self.t["state"]["resident"]["room_id"], "bookstore")

    def test_cross_midnight_hours(self):
        self.p["rooms"][0]["hours"] = {"open": 22, "close": 6}
        self.p["initial"].update(room_id="upstairs", time="2026-10-11T23:00")
        self.t = self.store.create_trial(self.p)
        self.action("move", "bookstore")

    def test_isolation_edit_export_and_import_in_clean_instance(self):
        self.action("pick_up", "old-book")
        self.action("read", "old-book")
        save = self.store.export_save(self.t["id"])
        self.p["meta"]["name"] = "修订后的世界"
        self.p["objects"] = [o for o in self.p["objects"] if o["id"] != "old-book"]
        self.store.save_draft(self.p, 0)
        self.assertEqual(self.store.trial(self.t["id"])["package"]["meta"]["name"], "雨天旧书店")
        exported = self.store.draft()["package"]
        self.assertNotIn("state", exported)
        fresh = Store(Path(self.tmp.name) / "clean-instance.db", exported)
        imported = fresh.create_trial(exported)
        self.assertEqual(imported["state"]["revision"], 0)
        restored = fresh.create_trial(save=save)
        self.assertEqual(restored["state"], save["state"])
        self.assertNotEqual(restored["id"], self.t["id"])
        self.assertNotIn("old-book", [o["id"] for o in fresh.draft()["package"]["objects"]])

    def test_draft_optimistic_conflict(self):
        self.store.save_draft(self.p, 0)
        with self.assertRaises(WorldError) as error:
            self.store.save_draft(self.p, 0)
        self.assertEqual(error.exception.code, "STALE")

    def test_workspace_identity_upgrade_preserves_legacy_draft_and_trial(self):
        self.action("pick_up", "old-book")
        self.p["meta"]["name"] = "升级前草稿"
        self.store.save_draft(self.p, 0)
        before_draft, before_trial = self.store.draft(), self.store.trial(self.t["id"])
        # Simulate the exact v0.1 schema, which had no editor_meta table.
        with self.store.connection() as db:
            db.execute("DROP TABLE editor_meta")
        upgraded = Store(self.db, self.p)
        identity = upgraded.workspace_id()
        self.assertEqual(upgraded.draft(), before_draft)
        self.assertEqual(upgraded.trial(self.t["id"]), before_trial)
        self.assertEqual(Store(self.db, self.p).workspace_id(), identity)
        self.assertNotEqual(Store(Path(self.tmp.name) / "other.db", self.p).workspace_id(), identity)

    def test_idempotent_replay_never_rewinds(self):
        command = {"action": "pick_up", "target": "old-book"}
        first = self.store.execute(self.t["id"], "once", 0, command)
        self.t = first
        self.action("read", "old-book")
        replay = self.store.execute(self.t["id"], "once", 0, command)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["state"]["revision"], 2)
        self.assertEqual(replay["event"], first["event"])
        with self.assertRaises(WorldError):
            self.store.execute(self.t["id"], "once", 0, {"action": "wait"})

    def test_two_writers_one_state_revision(self):
        def attempt(n):
            try:
                return self.store.execute(self.t["id"], f"writer-{n}", 0, {"action": "pick_up", "target": "old-book"})
            except WorldError as e:
                return e.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, (1, 2)))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertIn("STALE", results)
        self.assertEqual(self.store.trial(self.t["id"])["state"]["revision"], 1)

    def test_incompatible_package_and_no_script_fields(self):
        for change in (lambda p: p.update(requires="nyairo.production.v9"), lambda p: p.update(script="eval(1)"),
                       lambda p: p["objects"][1]["actions"].append("run_code")):
            p = copy.deepcopy(self.p)
            change(p)
            with self.assertRaises(WorldError):
                validate_package(p)

    def test_corrupt_save_hash_and_dangling_state(self):
        good = self.store.export_save(self.t["id"])
        for change in (lambda s: s.update(package_hash="0" * 64),
                       lambda s: s["state"]["resident"].update(held="missing"),
                       lambda s: s["state"]["objects"]["old-book"].update(placement="held"),
                       lambda s: s["state"]["resident"].update(pose="lying"),
                       lambda s: s["state"]["objects"]["old-book"].update(placement="container", container_id="soft-bed")):
            bad = copy.deepcopy(good)
            change(bad)
            with self.assertRaises(WorldError):
                validate_save(bad)

    def test_duplicate_json_and_nonfinite_rejected(self):
        for payload in ('{"name":1,"name":2}', '{"x":NaN}', 'broken'):
            with self.assertRaises(WorldError):
                parse_json(payload)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = create_server(self.tmp.name,example_file="rainy-bookstore.nyairo-world.json")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, data=None, headers=None):
        h = {"Content-Type": "application/json", "X-Nyairo-Editor": "1", **(headers or {})}
        body = json.dumps(data).encode() if data is not None else None
        try:
            with urlopen(Request(self.base + path, body, h)) as res:
                return res.status, res.read(), dict(res.headers)
        except HTTPError as e:
            with e:
                return e.code, e.read(), dict(e.headers)

    def test_http_end_to_end_and_clean_import(self):
        status, body, _ = self.request('/api/bootstrap')
        self.assertEqual(status, 200)
        p = json.loads(body)["draft"]["package"]
        status, body, _ = self.request('/api/trials', {"package": p})
        self.assertEqual(status, 201)
        t = json.loads(body)
        status, body, _ = self.request('/api/trials/' + t['id'] + '/actions',
                                     {"execution_id": "http-test", "revision": 0, "command": {"action": "pick_up", "target": "old-book"}})
        self.assertEqual(status, 200)
        status, body, _ = self.request('/api/trials/' + t['id'] + '/save')
        save = json.loads(body)
        status, body, _ = self.request('/api/saves/import', {"save": save})
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body)["state"]["resident"]["held"], "old-book")
        self.assertNotEqual(json.loads(body)["id"], t["id"])

    def test_local_only_csrf_and_static_allowlist(self):
        self.assertEqual(self.request('/api/bootstrap', headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request('/api/draft', {}, {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request('/api/draft', {}, {"X-Nyairo-Editor": ""})[0], 403)
        for path in ('/../data/editor.sqlite3', '/data/editor.sqlite3', '/server.py'):
            self.assertEqual(self.request(path)[0], 404)
        status, body, headers = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn(b'<!doctype html>', body)
        self.assertIn('frame-ancestors', headers['Content-Security-Policy'])

    def test_editor_module_and_workspace_identity_bootstrap(self):
        status, body, headers = self.request('/editor-state.mjs')
        self.assertEqual(status, 200)
        self.assertIn('javascript', headers['Content-Type'])
        self.assertIn(b'export class DraftHistory', body)
        a = json.loads(self.request('/api/bootstrap')[1])['workspace_id']
        b = json.loads(self.request('/api/bootstrap')[1])['workspace_id']
        self.assertEqual(a, b)
        self.assertEqual(len(a), 32)

    def test_rules_preview_and_durable_recovery_api(self):
        bootstrap=json.loads(self.request('/api/bootstrap')[1]);p=bootstrap['draft']['package']
        self.assertEqual(bootstrap['actions']['read']['parameters'][0]['key'],'minutes')
        self.assertEqual(self.request('/layout.mjs')[0],200)
        p['rooms'][0]['hours']={'open':22,'close':6}
        for minute,open_now in ((23*60,True),(5*60+59,True),(6*60,False),(21*60,False)):
            status,body,_=self.request('/api/rules/preview',{'package':p,'minute':minute})
            self.assertEqual(status,200);self.assertEqual(json.loads(body)['rooms'][0]['open'],open_now)
        status,_,_=self.request('/api/recovery',{'client_id':'http-copy','sequence':1,'package':p,'base_revision':0,'base_fingerprint':'base'})
        self.assertEqual(status,200);self.assertEqual(json.loads(self.request('/api/bootstrap')[1])['recoveries'][0]['package'],p)

    def test_invalid_import_does_not_replace_draft(self):
        before = self.request('/api/bootstrap')[1]
        self.assertEqual(self.request('/api/draft', {"revision": 0, "package": {"format": "bad"}})[0], 422)
        self.assertEqual(self.request('/api/bootstrap')[1], before)

    def test_export_download_has_attachment_and_exact_content(self):
        p = json.loads(self.request('/api/bootstrap')[1])["draft"]["package"]
        status, body, _ = self.request('/api/exports', {"kind": "world", "package": p})
        self.assertEqual(status, 201)
        info = json.loads(body)
        url = info["url"]
        status, payload, headers = self.request(url)
        self.assertEqual(status, 200)
        self.assertIn('attachment;', headers['Content-Disposition'])
        self.assertEqual(json.loads(payload), p)
        local = Path(self.tmp.name) / info['local_file']
        self.assertEqual(local.read_bytes(), payload)
        self.assertTrue((Path(self.tmp.name) / info['cover_file']).read_bytes().startswith(b'<svg'))

    def test_malformed_fields_return_explained_errors(self):
        p = json.loads(self.request('/api/bootstrap')[1])["draft"]["package"]
        for bad in ([], None, {"format": []}, {**p, "rooms": [None]}, {**p, "initial": []}):
            status, body, _ = self.request('/api/validate', {"package": bad})
            self.assertEqual(status, 422)
            self.assertIn("error", json.loads(body))


if __name__ == "__main__":
    unittest.main(verbosity=2)

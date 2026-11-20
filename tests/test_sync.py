"""Run with: python3 -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import claude_session_sync as css  # noqa: E402

A = "aaaaaaaa-0000-4000-8000-000000000001"
B = "bbbbbbbb-0000-4000-8000-000000000002"
C = "cccccccc-0000-4000-8000-000000000003"
ORG_A = "0a0a0a0a-0000-4000-8000-00000000000a"
ORG_B = "0b0b0b0b-0000-4000-8000-00000000000b"
ORG_C = "0c0c0c0c-0000-4000-8000-00000000000c"


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.base = root / "claude-code-sessions"
        self.config = root / "config.json"
        self.a = self.folder(A, ORG_A)
        self.b = self.folder(B, ORG_B)
        css.LOG = root / "sync.log"

    def tearDown(self):
        self.tmp.cleanup()

    def folder(self, account, org):
        path = self.base / account / org
        path.mkdir(parents=True)
        return path

    def signed_in(self, account):
        self.config.write_text(json.dumps({"lastKnownAccountUuid": account}))

    def record(self, folder, sid, activity=1, **extra):
        path = folder / ("local_%s.json" % sid)
        path.write_text(json.dumps(dict(sessionId="local_" + sid, lastActivityAt=activity, **extra)))
        return path

    def read(self, folder, sid):
        return json.loads((folder / ("local_%s.json" % sid)).read_text())

    def has(self, folder, sid):
        return (folder / ("local_%s.json" % sid)).exists()

    def sync(self, running, dry_run=False):
        return css.sync(self.base, self.config, running, dry_run)

    def test_running_copies_from_signed_in_account_only(self):
        self.signed_in(A)
        self.record(self.a, "s1", 5)
        self.record(self.b, "s2", 5)
        before = sorted(p.name for p in self.a.iterdir())
        self.sync(running=True)
        self.assertTrue(self.has(self.b, "s1"))
        self.assertEqual(sorted(p.name for p in self.a.iterdir()), before)
        self.assertEqual(os.stat(self.b / "local_s1.json").st_mode & 0o777, 0o600)

    def test_newer_activity_wins_regardless_of_mtime(self):
        self.signed_in(A)
        self.record(self.a, "s", 3)
        self.record(self.b, "s", 8)
        future = time.time() + 100
        os.utime(self.a / "local_s.json", (future, future))
        self.sync(running=True)
        self.assertEqual(self.read(self.b, "s")["lastActivityAt"], 8)

    def test_equal_activity_falls_back_to_mtime(self):
        self.signed_in(A)
        self.record(self.a, "s", 4, title="renamed")
        self.record(self.b, "s", 4, title="old")
        future = time.time() + 100
        os.utime(self.a / "local_s.json", (future, future))
        self.sync(running=True)
        self.assertEqual(self.read(self.b, "s")["title"], "renamed")

    def test_deletion_propagates(self):
        self.signed_in(A)
        self.record(self.b, "s", 1)
        (self.a / "deleted_s").write_text("1")
        self.sync(running=True)
        self.assertTrue((self.b / "deleted_s").exists())
        self.assertFalse(self.has(self.b, "s"))

    def test_deleted_session_is_not_resurrected(self):
        self.record(self.a, "s", 9)
        (self.b / "deleted_s").write_text("1")
        self.sync(running=False)
        self.assertFalse(self.has(self.a, "s"))
        self.assertFalse(self.has(self.b, "s"))

    def test_unreadable_or_mismatched_records_are_skipped(self):
        self.signed_in(A)
        (self.a / "local_bad.json").write_text("{half")
        (self.a / "local_other.json").write_text(json.dumps({"sessionId": "local_x"}))
        self.record(self.b, "bad", 2)
        self.sync(running=True)
        self.assertEqual(self.read(self.b, "bad")["lastActivityAt"], 2)
        self.assertFalse(self.has(self.b, "other"))

    def test_unknown_signed_in_account_skips(self):
        self.signed_in("not-a-uuid")
        self.record(self.a, "s", 1)
        self.assertEqual(self.sync(running=True), 0)
        self.assertFalse(self.has(self.b, "s"))

    def test_quit_does_full_merge_across_three_accounts(self):
        c = self.folder(C, ORG_C)
        self.record(self.a, "only-a", 1)
        self.record(self.b, "only-b", 1)
        self.record(c, "shared", 1)
        self.record(self.a, "shared", 7)
        self.sync(running=False)
        for folder in (self.a, self.b, c):
            self.assertTrue(self.has(folder, "only-a") and self.has(folder, "only-b"))
            self.assertEqual(self.read(folder, "shared")["lastActivityAt"], 7)
        self.assertEqual(self.sync(running=False), 0)

    def test_all_org_folders_of_signed_in_account_are_left_alone(self):
        second_org = self.folder(A, ORG_B)
        self.record(second_org, "own", 1)
        self.signed_in(A)
        self.record(self.b, "s", 1)
        self.sync(running=True)
        self.assertFalse(self.has(second_org, "s"))
        self.assertFalse(self.has(self.a, "s"))

    def test_empty_switch_leftover_folders_are_ignored(self):
        self.record(self.a, "s", 1)
        leftover = self.folder(A, ORG_B)
        self.assertEqual(css.discover(self.base)[A], [self.a])
        self.assertEqual(css.discover(self.base)[B], [self.b])
        self.sync(running=False)
        self.assertFalse(self.has(leftover, "s"))

    def test_new_account_folder_is_picked_up(self):
        self.signed_in(A)
        self.record(self.a, "s", 1)
        self.sync(running=True)
        c = self.folder(C, ORG_C)
        self.sync(running=True)
        self.assertTrue(self.has(c, "s"))

    def test_ignores_non_account_folders_and_symlinks(self):
        (self.base / "_shared").mkdir()
        (self.base / A / (ORG_A + ".bak")).mkdir()
        link_account = "dddddddd-0000-4000-8000-000000000004"
        (self.base / link_account).mkdir()
        (self.base / link_account / ORG_A).symlink_to(self.a)
        self.assertEqual(set(css.discover(self.base)), {A, B})
        self.assertEqual(len(css.symlinked_folders(self.base)), 1)

    def test_dry_run_writes_nothing(self):
        self.signed_in(A)
        self.record(self.a, "s", 1)
        (self.a / "deleted_t").write_text("1")
        self.assertEqual(self.sync(running=True, dry_run=True), 2)
        self.assertEqual(list(self.b.iterdir()), [])

    def test_no_temp_files_left(self):
        self.signed_in(A)
        self.record(self.a, "s", 1)
        self.sync(running=True)
        self.assertEqual(list(self.b.glob(".session-sync-*")), [])


if __name__ == "__main__":
    unittest.main()

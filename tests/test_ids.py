"""Task-ref resolution, displayed ids, and done-on-project, against a mocked API.

Run with: python3 -m unittest discover -s tests
"""

import argparse
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import marvin  # noqa: E402

RECUR = "628bcfd6-85e9-4925-bdac-019bcafd2560"
STRAY = f"2025-02-13_{RECUR}"
OCT27 = f"2026-10-27_{RECUR}"
IN_TWO_DAYS = (marvin.datetime.now().date() + marvin.timedelta(days=2)).isoformat()


def task(_id, title, day="unassigned", **extra):
    return {"_id": _id, "_rev": "1-abc", "title": title, "day": day, "db": "Tasks", **extra}


class FakeMarvin:
    """Routes api_request calls to fixed pools and records every POST."""

    def __init__(self, today=(), upcoming=None, children=None, docs=None, categories=(), fail=()):
        self.fail = set(fail)                   # (endpoint, date or parentId) that error
        self.today = list(today)
        self.upcoming = upcoming or {}          # date -> [items]
        self.children = children or {}          # parentId -> [items]
        self.docs = dict(docs or {})            # id -> doc
        self.categories = list(categories)
        self.posts = []

    def __call__(self, method, endpoint, data=None, full_access=False, tolerant=False, max_retries=5):
        if method == "POST":
            self.posts.append((endpoint, data))
            if endpoint == "doc/delete":
                self.docs.pop(data["itemId"], None)
            return None
        data = data or {}
        if (endpoint, data.get("date") or data.get("parentId")) in self.fail:
            assert tolerant, f"{endpoint} failure would have exited"
            return None
        if endpoint == "todayItems":
            return list(self.upcoming.get(data.get("date"), [])) if "date" in data else list(self.today)
        if endpoint in ("dueItems", "doneItems"):
            return []
        if endpoint == "categories":
            return list(self.categories)
        if endpoint == "children":
            return list(self.children.get(data.get("parentId"), []))
        if endpoint == "doc":
            doc = self.docs.get(data.get("id"))
            return doc if doc else {"error": "not_found"}
        raise AssertionError(f"unexpected {method} {endpoint}")


def run(func, **kwargs):
    """Run a cmd_* function; return (exit code or None, stdout)."""
    out = io.StringIO()
    code = None
    with contextlib.redirect_stdout(out):
        try:
            func(argparse.Namespace(**kwargs))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()


class RecurringIncident(unittest.TestCase):
    """The 10/25 incident: a stray backlog instance and a scheduled 10/27 one."""

    def setUp(self):
        self.stray = task(STRAY, "Review PRs", recurring=True, recurringTaskId=RECUR)
        self.oct27 = task(OCT27, "Review PRs", day="2026-10-27", recurring=True, recurringTaskId=RECUR)
        self.fake = FakeMarvin(
            # Two days out, so the upcoming-days sweep sees it whatever today is.
            upcoming={IN_TWO_DAYS: [self.oct27]},
            children={"unassigned": [self.stray]},
            docs={STRAY: self.stray, OCT27: self.oct27,
                  RECUR: {"_id": RECUR, "title": "Review PRs", "db": "RecurringTasks"}},
        )
        patcher = mock.patch.object(marvin, "api_request", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_backlog_shows_an_id_that_resolves_to_exactly_that_item(self):
        code, out = run(marvin.cmd_backlog, uncategorized=False, subtasks=False)
        self.assertIsNone(code)
        self.assertIn(f"({STRAY})", out)
        self.assertNotIn("(628bcfd6)", out)
        self.assertIs(marvin.resolve_task(STRAY), self.stray)

    def test_rm_refuses_a_shared_uuid_prefix_and_lists_instances(self):
        code, out = run(marvin.cmd_rm, task_id="628bcfd6", force=True)
        self.assertEqual(code, 1)
        self.assertEqual(self.fake.posts, [])
        self.assertIn(STRAY, out)
        self.assertIn(OCT27, out)

    def test_uuid_prefix_is_refused_even_with_one_instance_in_view(self):
        self.fake.children = {}
        code, out = run(marvin.cmd_rm, task_id="628bcfd6", force=True)
        self.assertEqual(code, 1)
        self.assertEqual(self.fake.posts, [])
        self.assertIn("names a recurring task", out)

    MUTATING = [
        (marvin.cmd_rm, dict(task_id="628bcfd6", force=True)),
        (marvin.cmd_done, dict(task_id="628bcfd6")),
        (marvin.cmd_move, dict(task_id="628bcfd6", date=["2026-10-30"], from_date=None)),
        (marvin.cmd_snooze, dict(task_id="628bcfd6", duration=["2h"])),
        (marvin.cmd_estimate, dict(task_id="628bcfd6", minutes=30)),
    ]

    def test_every_mutating_command_refuses_the_ambiguous_ref(self):
        for func, kwargs in self.MUTATING:
            with self.subTest(func.__name__):
                code, out = run(func, **kwargs)
                self.assertEqual(code, 1, out)
                self.assertEqual(self.fake.posts, [], out)
                self.assertIn("Ambiguous", out)

    def test_every_mutating_command_refuses_with_one_instance_in_view(self):
        self.fake.children = {}
        for func, kwargs in self.MUTATING:
            with self.subTest(func.__name__):
                code, out = run(func, **kwargs)
                self.assertEqual(code, 1, out)
                self.assertEqual(self.fake.posts, [], out)
                self.assertIn("names a recurring task", out)

    def test_full_template_uuid_is_refused(self):
        code, out = run(marvin.cmd_rm, task_id=RECUR, force=True)
        self.assertEqual(code, 1)
        self.assertEqual(self.fake.posts, [])
        self.assertIn("is the recurring task", out)

    def test_dated_id_deletes_only_that_instance(self):
        code, out = run(marvin.cmd_rm, task_id=STRAY, force=True)
        self.assertIsNone(code, out)
        self.assertEqual(self.fake.posts, [("doc/delete", {"itemId": STRAY, "itemVersion": "1-abc"})])
        self.assertIn(OCT27, self.fake.docs)

    def test_dated_prefix_resolves(self):
        self.assertIs(marvin.resolve_task("2025-02-13_628b"), self.stray)


class PrefixAcrossPools(unittest.TestCase):
    """A prefix that matches in today's list must still be checked everywhere."""

    def setUp(self):
        self.today = task("0056d165b575fe337e8f12d5aaaa", "Today task", day="2026-10-25")
        self.unsched = task("0056d165b575fe337e8f12d5bbbb", "Backlog task")
        self.project = {"_id": "12174a17-f2b4-49cc-bfb0-ba57ae839ff2", "_rev": "3-x",
                        "title": "Cancel Wistia", "type": "project"}
        self.fake = FakeMarvin(
            today=[self.today],
            children={"unassigned": [self.unsched, self.project]},
            docs={self.today["_id"]: self.today, self.unsched["_id"]: self.unsched,
                  self.project["_id"]: self.project},
        )
        patcher = mock.patch.object(marvin, "api_request", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_prefix_matching_tasks_in_two_pools_is_refused(self):
        for func, kwargs in [(marvin.cmd_rm, dict(task_id="0056d165", force=True)),
                             (marvin.cmd_done, dict(task_id="0056d165"))]:
            with self.subTest(func.__name__):
                code, out = run(func, **kwargs)
                self.assertEqual(code, 1, out)
                self.assertEqual(self.fake.posts, [])
                self.assertIn("matches 2 tasks", out)

    def test_unique_prefix_still_resolves(self):
        code, out = run(marvin.cmd_estimate, task_id="0056d165b575fe337e8f12d5b", minutes=30)
        self.assertIsNone(code, out)
        self.assertEqual(self.fake.posts[0][1]["itemId"], self.unsched["_id"])

    def test_done_on_a_task_uses_markDone(self):
        code, out = run(marvin.cmd_done, task_id="0056d165b575fe337e8f12d5a")
        self.assertIsNone(code, out)
        self.assertEqual([p[0] for p in self.fake.posts], ["markDone"])
        self.assertEqual(self.fake.posts[0][1]["itemId"], self.today["_id"])

    def test_done_on_a_project_uses_doc_update(self):
        code, out = run(marvin.cmd_done, task_id="12174a17")
        self.assertIsNone(code, out)
        self.assertEqual(len(self.fake.posts), 1)
        endpoint, data = self.fake.posts[0]
        self.assertEqual(endpoint, "doc/update")
        self.assertEqual(data["itemId"], self.project["_id"])
        setters = {s["key"]: s["val"] for s in data["setters"]}
        self.assertIs(setters["done"], True)
        self.assertIsInstance(setters["doneAt"], int)
        self.assertEqual(setters["fieldUpdates.done"], setters["doneAt"])
        self.assertIn("(project)", out)


class ReviewFollowUps(unittest.TestCase):
    """Reorder prefixes, failed fetches, subtasks outside today, full regular ids."""

    def setUp(self):
        self.a = task("abcdef12aaaaaaaaaaaaaaaaaaaaaaaa", "A", day="2026-10-25")
        self.b = task("abcdef12bbbbbbbbbbbbbbbbbbbbbbbb", "B", day="2026-10-25")
        self.fake = FakeMarvin(today=[self.a, self.b], docs={self.a["_id"]: self.a, self.b["_id"]: self.b})
        patcher = mock.patch.object(marvin, "api_request", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reorder_refuses_a_shared_8_char_prefix(self):
        code, out = run(marvin.cmd_reorder, task_ids=["abcdef12"])
        self.assertEqual(code, 1, out)
        self.assertEqual(self.fake.posts, [])
        self.assertIn("Ambiguous", out)

    def test_reorder_accepts_a_unique_prefix(self):
        code, out = run(marvin.cmd_reorder, task_ids=["abcdef12b"])
        self.assertIsNone(code, out)
        self.assertEqual({d["itemId"] for _, d in self.fake.posts}, {self.b["_id"]})

    def test_failed_sweep_fetch_refuses_instead_of_resolving(self):
        self.fake.today = [self.a]
        self.fake.fail = {("children", "unassigned")}
        code, out = run(marvin.cmd_rm, task_id="abcdef12", force=True)
        self.assertEqual(code, 1, out)
        self.assertEqual(self.fake.posts, [])
        self.assertIn("partial list", out)

    def test_subtask_of_an_upcoming_task_makes_the_ref_ambiguous(self):
        parent = task("ffff0000ffff0000ffff0000ffff0000", "Parent", day=IN_TWO_DAYS,
                      subtasks={"abcdef12cc": {"_id": "abcdef12cc", "title": "Sub"}})
        self.fake.today = [self.a]
        self.fake.upcoming = {IN_TWO_DAYS: [parent]}
        code, out = run(marvin.cmd_done, task_id="abcdef12")
        self.assertEqual(code, 1, out)
        self.assertEqual(self.fake.posts, [])

    def test_full_length_regular_id_resolves_by_doc_lookup(self):
        self.fake.today = []
        with mock.patch.object(marvin, "upcoming_task_pool", side_effect=AssertionError("swept")):
            self.assertIs(marvin.resolve_task(self.b["_id"]), self.b)


class DisplayIds(unittest.TestCase):
    def test_recurring_instances_display_their_full_id(self):
        items = [task(STRAY, "a"), task(OCT27, "b"), task("abcdef1234567890", "c")]
        uids = marvin.unique_ids(items)
        self.assertEqual(uids[STRAY], STRAY)
        self.assertEqual(uids[OCT27], OCT27)
        self.assertEqual(uids["abcdef1234567890"], "abcdef12")
        self.assertEqual(marvin.short_id(STRAY), STRAY)
        self.assertEqual(marvin.short_id("abcdef1234567890"), "abcdef12")


if __name__ == "__main__":
    unittest.main()

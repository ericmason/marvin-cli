"""Task-ref resolution, displayed ids, and done-on-project, against a mocked API.

Run with: python3 -m unittest discover -s tests
"""

import argparse
import contextlib
import io
import json
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


PAST_DAY = (marvin.datetime.now().date() - marvin.timedelta(days=95)).isoformat()
TODAY = marvin.datetime.now().strftime("%Y-%m-%d")


class PastDaysDigitIdsAndMoveFrom(unittest.TestCase):
    """Ids from a past day's listing, all-digit ids, and 'move --from'."""

    def setUp(self):
        # On a past day only: in no swept pool, reachable by doc lookup alone.
        self.old = task("02b9e38f1c4d4b6e9a7f0c2d3e4f5a6b", "July task", day=PAST_DAY)
        self.digits = task("27102153884401", "Digit-id task")
        self.a = task("abcdef12aaaaaaaaaaaaaaaaaaaaaaaa", "A", day="2026-10-25")
        self.b = task("bbcdef12bbbbbbbbbbbbbbbbbbbbbbbb", "B", day="2026-10-25")
        self.fake = FakeMarvin(
            today=[self.a, self.b],
            upcoming={PAST_DAY: [self.old], TODAY: [self.a, self.b]},
            children={"unassigned": [self.digits]},
            docs={t["_id"]: t for t in (self.old, self.digits, self.a, self.b)},
        )
        patcher = mock.patch.object(marvin, "api_request", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def shown_id(self, date):
        code, out = run(marvin.cmd_day, date=[date], incomplete=True, all=False,
                        snoozed=False, subtasks=False)
        self.assertIsNone(code, out)
        return out.split("(")[-1].split(")")[0]

    def test_past_day_listing_prints_the_full_id(self):
        self.assertEqual(self.shown_id(PAST_DAY), self.old["_id"])

    def test_upcoming_day_listing_keeps_the_short_id(self):
        self.fake.upcoming = {IN_TWO_DAYS: [self.old]}
        self.assertEqual(self.shown_id(IN_TWO_DAYS), "02b9e38f")

    def test_every_command_resolves_the_id_a_past_day_listing_prints(self):
        ref = self.shown_id(PAST_DAY)
        for func, kwargs in [(marvin.cmd_done, dict(task_id=ref)),
                             (marvin.cmd_snooze, dict(task_id=ref, duration=["2h"])),
                             (marvin.cmd_estimate, dict(task_id=ref, minutes=30)),
                             (marvin.cmd_rm, dict(task_id=ref, force=True))]:
            with self.subTest(func.__name__):
                self.fake.posts = []
                code, out = run(func, **kwargs)
                self.assertIsNone(code, out)
                self.assertEqual(self.fake.posts[0][1]["itemId"], self.old["_id"])

    def test_short_past_day_prefix_is_not_found(self):
        code, out = run(marvin.cmd_done, task_id="02b9e38f")
        self.assertEqual(code, 1, out)
        self.assertEqual(self.fake.posts, [])
        self.assertIn("'marvin day <date>' prints its full id", out)

    def test_all_digit_id_is_resolved_as_an_id(self):
        code, out = run(marvin.cmd_done, task_id="27102153")
        self.assertIsNone(code, out)
        self.assertNotIn("Invalid task number", out)
        self.assertEqual(self.fake.posts, [("markDone", {"itemId": self.digits["_id"],
                                                          "timeZoneOffset": marvin.get_tz_offset()})])

    def test_short_number_in_range_is_a_list_number(self):
        code, out = run(marvin.cmd_done, task_id="2")
        self.assertIsNone(code, out)
        self.assertEqual(self.fake.posts[0][1]["itemId"], self.b["_id"])

    def test_list_number_rules(self):
        items = [self.a, self.b]
        self.assertEqual(marvin.list_number("1", items), 1)
        self.assertIsNone(marvin.list_number("3", items))
        self.assertIsNone(marvin.list_number("0", items))
        self.assertIsNone(marvin.list_number("0002", items))
        self.assertIsNone(marvin.list_number("27102153", items))
        self.assertIsNone(marvin.list_number("abc", items))

    def main(self, *argv):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["marvin", *argv]), contextlib.redirect_stdout(out):
            try:
                marvin.main()
            except SystemExit as e:
                return e.code, out.getvalue()
        return None, out.getvalue()

    def test_move_refuses_an_unquoted_multi_word_from(self):
        self.fake.today.append(self.old)  # resolvable without --from
        for argv in [("move", "02b9e38f", "--from", "next", "monday"),
                     ("move", "02b9e38f", "--from", "3", "days", "ago")]:
            with self.subTest(argv):
                code, out = self.main(*argv)
                self.assertEqual(code, 1, out)
                self.assertEqual(self.fake.posts, [])
                self.assertIn("Error: Could not parse", out)

    def test_quoted_multi_word_from_works(self):
        three_ago = (marvin.datetime.now().date() - marvin.timedelta(days=3)).isoformat()
        self.fake.upcoming = {three_ago: [self.old]}
        code, out = self.main("move", "02b9e38f", "--from", "3 days ago", "tomorrow")
        self.assertIsNone(code, out)
        self.assertEqual(self.fake.posts[0][1]["itemId"], self.old["_id"])

    def test_past_day_embedded_subtask_keeps_its_short_id(self):
        sub_id = "f00dfeed" + "0" * 24
        self.old["subtasks"] = {sub_id: {"_id": sub_id, "title": "Sub", "done": False}}
        code, out = run(marvin.cmd_day, date=[PAST_DAY], incomplete=True, all=False,
                        snoozed=False, subtasks=False)
        self.assertIsNone(code, out)
        self.assertIn(f"({self.old['_id']})", out)
        self.assertIn("(f00dfeed)", out)

    def test_move_from_takes_one_date_in_any_position(self):
        target = IN_TWO_DAYS
        for argv in [("move", "02b9e38f", "--from", PAST_DAY),
                     ("move", "02b9e38f", "--from", PAST_DAY, target),
                     ("move", "02b9e38f", target, "--from", PAST_DAY),
                     ("move", "--from", PAST_DAY, "02b9e38f", target)]:
            with self.subTest(argv):
                self.fake.posts = []
                code, out = self.main(*argv)
                self.assertIsNone(code, out)
                endpoint, data = self.fake.posts[0]
                self.assertEqual((endpoint, data["itemId"]), ("doc/update", self.old["_id"]))
                day = {s["key"]: s["val"] for s in data["setters"]}["day"]
                expected = target if len(argv) == 5 else marvin.datetime.now().strftime("%Y-%m-%d")
                self.assertEqual(day, expected)


class DoneByListNumber(unittest.TestCase):
    """'marvin done N' completes the task 'marvin today' printed as N."""

    def setUp(self):
        later = int((marvin.datetime.now() + marvin.timedelta(hours=3)).timestamp() * 1000)
        # API order differs from rank order; one task is snoozed, one is a child,
        # and the server's undated todayItems and the due list hold other tasks.
        self.listed = [
            task("b3f81f9e000000000000000000000000", "CLAUDE.md task", day=TODAY, rank=30),
            task("5a0000000000000000000000000000aa", "Snoozed", day=TODAY, rank=5,
                 itemSnoozeTime=later),
            task("8326f130000000000000000000000000", "Top task", day=TODAY, rank=10),
            task("c4000000000000000000000000000000", "Child", day=TODAY, rank=1,
                 parentId="8326f130000000000000000000000000"),
            task("d5000000000000000000000000000000", "Middle", day=TODAY, rank=20),
        ]
        self.fake = FakeMarvin(
            today=[task("e6000000000000000000000000000000", "Server-today only", rank=0)],
            upcoming={TODAY: self.listed},
        )
        patcher = mock.patch.object(marvin, "api_request", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_number_today_prints_completes_that_task(self):
        code, out = run(marvin.cmd_today, date=None, all=False, snoozed=False, subtasks=False)
        self.assertIsNone(code, out)
        shown = {}
        for line in out.splitlines():
            head = line.strip().split(". ", 1)
            if head[0].isdigit():
                shown[head[0]] = line.rsplit("(", 1)[1].rstrip(")")
        self.assertEqual(list(shown.values()), ["8326f130", "d5000000", "b3f81f9e"])
        by_id = {t["_id"]: t for t in self.listed}
        for number, sid in shown.items():
            with self.subTest(number):
                self.fake.posts = []
                code, out = run(marvin.cmd_done, task_id=number)
                self.assertIsNone(code, out)
                done_id = self.fake.posts[0][1]["itemId"]
                self.assertTrue(done_id.startswith(sid), (number, done_id))
                self.assertIn(done_id, by_id)

    def test_number_past_the_list_is_refused_even_when_it_prefixes_an_id(self):
        # "12" fits no list position but is a unique prefix of a backlog task.
        self.fake.children = {"unassigned": [task("12aa0000000000000000000000000000", "Backlog")]}
        for ref in ("4", "12", "1234567"):
            with self.subTest(ref):
                code, out = run(marvin.cmd_done, task_id=ref)
                self.assertEqual(code, 1, out)
                self.assertEqual(self.fake.posts, [])
                self.assertIn("Invalid task number", out)


class DisplayIds(unittest.TestCase):
    def test_recurring_instances_display_their_full_id(self):
        items = [task(STRAY, "a"), task(OCT27, "b"), task("abcdef1234567890", "c")]
        uids = marvin.unique_ids(items)
        self.assertEqual(uids[STRAY], STRAY)
        self.assertEqual(uids[OCT27], OCT27)
        self.assertEqual(uids["abcdef1234567890"], "abcdef12")
        self.assertEqual(marvin.short_id(STRAY), STRAY)
        self.assertEqual(marvin.short_id("abcdef1234567890"), "abcdef12")


REAL_SYNC_CREDENTIALS = marvin.sync_credentials
_no_sync = mock.patch.object(marvin, "sync_credentials", lambda: None)


def setUpModule():
    """Default every test to no sync credentials, whatever this machine's config
    holds, so no test can reach a real sync database. The tests that exercise the
    sync route patch it again themselves."""
    _no_sync.start()


def tearDownModule():
    _no_sync.stop()


class FakeSyncDB:
    """Stands in for the sync database at marvin's single HTTP seam.

    Behaves like CouchDB where that matters to the caller:

    - a Mango operator on a missing field never matches, so {"$ne": True} on a
      doc with no done field returns nothing;
    - _all_docs ignores startkey_docid unless startkey is set;
    - page_size caps rows per page, which a server may do below the requested
      limit, so a short page does not mean the end of the data.

    find_fails=True makes _find unavailable; status fails every request with
    that HTTP status.
    """

    CREDS = {
        "sync_server": "https://sync.example.com",
        "sync_database": "db-eric",
        "sync_user": "eric",
        "sync_password": "secret",
    }

    def __init__(self, docs, find_fails=False, page_size=None, status=None):
        self.docs = list(docs)
        self.find_fails = find_fails
        self.page_size = page_size
        self.status = status
        self.calls = []

    def install(self, testcase):
        for name, value in (("sync_credentials", lambda: dict(self.CREDS)),
                            ("sync_http", self)):
            patcher = mock.patch.object(marvin, name, value)
            patcher.start()
            testcase.addCleanup(patcher.stop)
        # marvin caches one read per command run; each test is its own run.
        marvin._sync_cache.clear()
        testcase.addCleanup(marvin._sync_cache.clear)
        marvin._sync_warned = False
        testcase.addCleanup(setattr, marvin, "_sync_warned", False)
        return self

    def matching(self, body):
        """The docs a Mango selector of the shape marvin sends would return."""
        out = list(self.docs)
        for field, condition in body["selector"].items():
            if "$in" in condition:
                out = [d for d in out if d.get(field) in condition["$in"]]
            elif "$ne" in condition:
                # A missing field matches no operator, $exists aside.
                out = [d for d in out
                       if field in d and d.get(field) != condition["$ne"]]
            else:
                raise AssertionError(f"unsupported condition {condition}")
        return out

    def page(self, rows, limit):
        return rows[:min(limit, self.page_size or limit)]

    def __call__(self, method, url, auth, body=None, params=None):
        self.calls.append((method, url, body, params))
        assert auth == (self.CREDS["sync_user"], self.CREDS["sync_password"])
        if self.status:
            marvin.sync_note_error(f"HTTP {self.status}")
            return None
        if url.endswith("/_find"):
            if self.find_fails:
                marvin.sync_note_error("HTTP 404")
                return None
            hits = self.matching(body)
            start = int(body.get("bookmark") or 0)
            page = self.page(hits[start:], body["limit"])
            return {"docs": page, "bookmark": str(start + len(page))}
        if url.endswith("/_all_docs"):
            ordered = sorted(self.docs, key=lambda d: d["_id"])
            startkey = params.get("startkey")
            if startkey is not None:
                ordered = [d for d in ordered if d["_id"] >= json.loads(startkey)]
                ordered = ordered[params.get("skip", 0):]
            rows = [{"id": d["_id"], "doc": d}
                    for d in self.page(ordered, params["limit"])]
            return {"rows": rows}
        raise AssertionError(f"unexpected sync call {method} {url}")


class SearchIncludesBacklog(unittest.TestCase):
    def setUp(self):
        self.backlog = task("bl1", "Jon-Luke battle.net: tell Nick")
        self.filed = task("bl2", "Start the audit with Mythos")
        self.done = task("bl3", "Old battle task", done=True)
        self.fake = FakeMarvin(
            children={"unassigned": [self.backlog, self.done], "cat1": [self.filed, self.backlog]},
            categories=[{"_id": "cat1", "title": "Security"}],
        )
        patcher = mock.patch.object(marvin, "api_request", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def search(self, *words):
        return run(marvin.cmd_search, query=list(words), all=False)

    def test_finds_uncategorized_backlog_task(self):
        code, out = self.search("battle")
        self.assertIsNone(code)
        self.assertIn("battle.net", out)
        self.assertNotIn("Old battle task", out)

    def test_finds_categorized_backlog_task(self):
        _, out = self.search("Mythos")
        self.assertIn("Start the audit with Mythos", out)

    def test_dedupes_by_id_and_stays_read_only(self):
        _, out = self.search("battle")
        self.assertEqual(out.count("battle.net"), 1)
        self.assertEqual(self.fake.posts, [])


YESTERDAY = (marvin.datetime.now().date() - marvin.timedelta(days=1)).isoformat()


class SearchPastTheApiCap(unittest.TestCase):
    """The 11/8 bug: three open tasks that 'marvin search' called "No tasks found".

    The children endpoint returns at most CHILDREN_CAP items per parent, sorted
    by _id ascending, with no way to page past them, so every root task whose id
    sorts above the cut is unreachable through the API. All three missing tasks
    had such ids; a task with a low id ("Trello", 09be...) was found all along.
    """

    FILLER = [task(f"0{n:031x}", f"filler {n}") for n in range(marvin.CHILDREN_CAP)]
    HIDDEN = [
        task("b72ef38a" + "0" * 24, "Jon-Luke battle.net: tell Nick"),
        task("d22b8428" + "0" * 24, "Mythos company template audit"),
        task("e0000000" + "0" * 24, "Send Tom the site-leads skill (Tom meeting 11/3)"),
    ]

    def setUp(self):
        # The API serves only what fits under the cap: the filler, never HIDDEN.
        self.fake = FakeMarvin(children={"unassigned": list(self.FILLER)})
        patcher = mock.patch.object(marvin, "api_request", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def search(self, *words, **kw):
        return run(marvin.cmd_search, query=list(words), all=kw.get("all", False))

    def test_api_route_reports_the_cap_instead_of_claiming_nothing_matched(self):
        for term in ("battle", "mythos", "site-leads"):
            with self.subTest(term):
                code, out = self.search(term)
                self.assertIsNone(code, out)
                self.assertIn("No tasks found", out)
                self.assertIn(f"{marvin.CHILDREN_CAP}-item API cap hit", out)
                self.assertIn("(uncategorized)", out)
                self.assertIn("marvin setup", out)

    def test_no_cap_warning_when_every_list_fits(self):
        self.fake.children = {"unassigned": list(self.FILLER[:5])}
        _, out = self.search("filler 1")
        self.assertNotIn("API cap", out)

    def test_sync_route_finds_every_hidden_task(self):
        FakeSyncDB(self.FILLER + self.HIDDEN).install(self)
        for term, title in (
            ("battle", "battle.net"),
            ("mythos", "Mythos company template audit"),
            ("site-leads", "Send Tom the site-leads skill"),
        ):
            with self.subTest(term):
                code, out = self.search(term)
                self.assertIsNone(code, out)
                self.assertIn(title, out)
                self.assertNotIn("API cap", out)
        self.assertEqual(self.fake.posts, [])

    def test_sync_route_pages_until_the_database_is_exhausted(self):
        many = [task(f"9{n:031x}", f"bulk {n} battle") for n in range(marvin.SYNC_PAGE * 2 + 7)]
        db = FakeSyncDB(many).install(self)
        _, out = self.search("bulk 1200 battle")
        self.assertIn("bulk 1200 battle", out)
        # Three full-ish pages, then one empty page to prove the end was reached.
        self.assertEqual(len(db.calls), 4)
        self.assertEqual([c[2]["bookmark"] for c in db.calls[1:]],
                         [str(marvin.SYNC_PAGE), str(marvin.SYNC_PAGE * 2), str(len(many))])

    def test_sync_database_is_read_once_per_run(self):
        db = FakeSyncDB(self.HIDDEN).install(self)
        first = marvin.sync_all_items()
        reads = len(db.calls)
        self.assertEqual(first, marvin.sync_all_items())
        self.assertEqual(len(db.calls), reads)

    def test_sync_route_leaves_done_tasks_out_unless_all_is_passed(self):
        db = FakeSyncDB(self.HIDDEN + [task("f" * 32, "Old battle task", done=True)])
        db.install(self)
        _, out = self.search("battle")
        self.assertNotIn("Old battle task", out)
        _, out = self.search("battle", all=True)
        self.assertIn("Old battle task", out)

    def test_falls_back_to_all_docs_when_find_is_unavailable(self):
        db = FakeSyncDB(self.HIDDEN + [{"_id": "z1", "db": "TimeData", "title": "mythos noise"}],
                        find_fails=True).install(self)
        _, out = self.search("mythos")
        self.assertIn("Mythos company template audit", out)
        self.assertNotIn("mythos noise", out)  # only Tasks and Categories count
        self.assertTrue(any(c[1].endswith("/_all_docs") for c in db.calls))

    def test_search_covers_tasks_left_on_a_past_day(self):
        stale = task("c" * 32, "battle.net follow-up", day=YESTERDAY)
        self.fake.upcoming = {YESTERDAY: [stale]}
        _, out = self.search("battle.net")
        self.assertIn("battle.net follow-up", out)
        # Outside every pool resolve_task searches by prefix, so the full id shows.
        self.assertIn("c" * 32, out)

    def test_sync_route_keeps_tasks_that_have_no_done_field(self):
        # Every fixture here is a doc with no done field, which is what a Mango
        # {"done": {"$ne": True}} selector would have dropped.
        db = FakeSyncDB(self.HIDDEN).install(self)
        self.assertEqual(len(marvin.sync_all_items()), len(self.HIDDEN))
        selectors = [call[2]["selector"] for call in db.calls]
        self.assertTrue(all("done" not in s for s in selectors), selectors)

    def test_all_docs_route_pages_past_one_server_page(self):
        many = [task(f"{n:032x}", f"bulk {n}") for n in range(marvin.SYNC_PAGE * 2 + 37)]
        FakeSyncDB(many, find_fails=True).install(self)
        got = marvin.sync_all_items()
        self.assertEqual(len(got), len(many))
        ids = [d["_id"] for d in got]
        self.assertEqual(len(set(ids)), len(ids))  # no key repeated by the overlap
        self.assertEqual(set(ids), {d["_id"] for d in many})

    def test_a_server_page_smaller_than_the_requested_limit_is_not_the_end(self):
        many = [task(f"{n:032x}", f"bulk {n}") for n in range(31)]
        for route in ({}, {"find_fails": True}):
            with self.subTest(route=route or "_find"):
                marvin._sync_cache.clear()
                FakeSyncDB(many, page_size=7, **route).install(self)
                self.assertEqual(len(marvin.sync_all_items()), len(many))

    def test_a_failed_sync_read_still_shows_the_full_id_of_a_past_day_hit(self):
        # Credentials configured but the read failed, so the API route is in
        # use and a past-day prefix would not resolve.
        stale = task("c" * 32, "battle.net follow-up", day=YESTERDAY)
        self.fake.upcoming = {YESTERDAY: [stale]}
        FakeSyncDB(self.HIDDEN, status=401).install(self)
        _, out = self.search("battle.net")
        self.assertIn("battle.net follow-up", out)
        self.assertIn("c" * 32, out)
        self.assertIn("Could not read the sync database (HTTP 401)", out)

    def test_backlog_uses_the_sync_route_and_groups_by_category(self):
        filed = task("a" * 32, "Mythos audit", parentId="cat1")
        self.fake.categories = [{"_id": "cat1", "title": "Security"}]
        FakeSyncDB(self.HIDDEN + [filed]).install(self)
        code, out = run(marvin.cmd_backlog, uncategorized=False, subtasks=False)
        self.assertIsNone(code, out)
        self.assertIn("4 open task(s)", out)
        self.assertIn("# Security  (1)", out)
        self.assertIn("Mythos audit", out)
        self.assertNotIn("API cap", out)


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.payload = payload

    def json(self):
        if self.payload is None:
            raise json.JSONDecodeError("no json", "", 0)
        return self.payload


class SyncHttpRetries(unittest.TestCase):
    """What sync_http retries, and what it treats as a final answer."""

    def setUp(self):
        self.posts = []
        self.gets = []
        self.sleeps = []
        for name, record in (("post", self.posts), ("get", self.gets)):
            patcher = mock.patch.object(
                marvin.requests, name,
                lambda url, record=record, **kw: (record.append(url), self.reply)[1],
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(marvin.time, "sleep", self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(
            marvin, "sync_credentials", lambda: dict(FakeSyncDB.CREDS))
        patcher.start()
        self.addCleanup(patcher.stop)
        marvin._sync_cache.clear()
        self.addCleanup(marvin._sync_cache.clear)
        marvin._sync_warned = False
        self.addCleanup(setattr, marvin, "_sync_warned", False)

    def test_a_rejected_credential_is_not_retried_and_the_failure_is_cached(self):
        self.reply = FakeResponse(401)
        code, out = run(lambda a: print(marvin.sync_all_items()), x=None)
        self.assertIsNone(code, out)
        # One _find, then one _all_docs fallback. No retry of either, no sleep.
        self.assertEqual((len(self.posts), len(self.gets)), (1, 1))
        self.assertEqual(self.sleeps, [])
        self.assertIn("Could not read the sync database (HTTP 401)", out)
        # Cached: the next helper in the same run re-reads nothing.
        self.assertIsNone(marvin.sync_all_items())
        self.assertEqual((len(self.posts), len(self.gets)), (1, 1))

    def test_a_server_error_is_retried(self):
        self.reply = FakeResponse(503)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(marvin.sync_all_items())
        # Four attempts on _find, then four on the _all_docs fallback.
        self.assertEqual((len(self.posts), len(self.gets)), (4, 4))
        self.assertEqual(len(self.sleeps), 6)

    def test_a_404_falls_back_to_all_docs_without_retrying_find(self):
        self.reply = FakeResponse(404)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(marvin.sync_all_items())
        self.assertEqual((len(self.posts), len(self.gets)), (1, 1))


class SyncCredentialHygiene(unittest.TestCase):
    def setUp(self):
        marvin._sync_cache.clear()
        self.addCleanup(marvin._sync_cache.clear)
        marvin._sync_warned = False
        self.addCleanup(setattr, marvin, "_sync_warned", False)

    def test_a_plain_http_sync_server_is_refused(self):
        creds = dict(FakeSyncDB.CREDS, sync_server="http://sync.example.com")
        with mock.patch.object(marvin, "load_config", lambda: creds), \
             mock.patch.object(marvin, "sync_credentials", REAL_SYNC_CREDENTIALS):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertIsNone(marvin.sync_credentials())
        self.assertIn("not an https:// URL", out.getvalue())

    def test_config_is_written_at_0600(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = marvin.Path(tmp) / "nested" / "config.json"
            with mock.patch.object(marvin, "CONFIG_DIR", path.parent), \
                 mock.patch.object(marvin, "CONFIG_FILE", path):
                marvin.save_config({"api_token": "t"})
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(path.read_text()), {"api_token": "t"})


if __name__ == "__main__":
    unittest.main()

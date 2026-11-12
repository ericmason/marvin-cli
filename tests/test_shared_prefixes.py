"""Ids that share a long prefix, from the 11/12 wrong-task-done incident.

Two tasks created together got ids that agree for 28 characters. A caller
shortened one to 8 characters, `marvin done` refused it as ambiguous, the error
was cut down with `| tail -3` to a single id line, and that id belonged to the
other task. These tests pin the three fixes: listings never print the same id
twice, the ambiguity error puts each full id on the same line as its title, and
`marvin undone` reopens a task completed by mistake.

Run with: python3 -m unittest discover -s tests
"""

import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import marvin  # noqa: E402
from test_ids import FakeMarvin, run, task  # noqa: E402

NELSON = "605a9bf663c9e42e1074976da8e3b4c6"
ACCESS = "605a9bf663c9e42e1074976da8e3c1c9"
# A third id that shares only the first 8 characters.
OTHER = "605a9bf6ffffffffffffffffffffffff"
NELSON_TITLE = "Send Nelson the self-formatting spreadsheet demo link"
ACCESS_TITLE = ("Store the reason for each access exception in Hourglass, tied to the "
                "access request, before the January access review (Chris 1:1 11/11)")
TODAY = marvin.datetime.now().strftime("%Y-%m-%d")
YESTERDAY = (marvin.datetime.now().date() - marvin.timedelta(days=1)).isoformat()

ROW_ID = re.compile(r"\(([0-9a-f]{8,}|\d{4}-\d{2}-\d{2}_[0-9a-f-]+)\)")


class DoneAwareMarvin(FakeMarvin):
    """FakeMarvin plus per-date doneItems, dueItems, and labels."""

    def __init__(self, done=None, due=(), labels=(), **kw):
        super().__init__(**kw)
        self.done = done or {}      # date -> [items]
        self.due = list(due)
        self.labels = list(labels)

    def __call__(self, method, endpoint, data=None, full_access=False, tolerant=False, max_retries=5):
        if method == "GET" and endpoint == "doneItems":
            return list(self.done.get((data or {}).get("date"), []))
        if method == "GET" and endpoint == "dueItems":
            return list(self.due)
        if method == "GET" and endpoint == "labels":
            return list(self.labels)
        return super().__call__(method, endpoint, data, full_access, tolerant, max_retries)


def three_tasks(**extra):
    return [
        task(NELSON, NELSON_TITLE, TODAY, **extra),
        task(ACCESS, ACCESS_TITLE, TODAY, **extra),
        task(OTHER, "Unrelated task", TODAY, **extra),
    ]


class ListingIdsAreUnique(unittest.TestCase):
    """Every listing prints, per row, an id no other row in it shares."""

    def assert_unique_rows(self, out, full_ids):
        shown = ROW_ID.findall(out)
        self.assertEqual(len(shown), len(full_ids), out)
        self.assertEqual(len(set(shown)), len(shown), f"duplicate ids in:\n{out}")
        for sid in shown:
            hits = [f for f in full_ids if f.startswith(sid)]
            self.assertEqual(len(hits), 1, f"{sid} names {hits} in:\n{out}")
        self.assertNotIn("(605a9bf6)", out)

    def listing(self, func, fake, **kwargs):
        with mock.patch.object(marvin, "api_request", fake), \
                mock.patch.object(marvin, "sync_all_items", lambda *a, **k: None):
            code, out = run(func, **kwargs)
        self.assertIsNone(code, out)
        return out

    def test_today_and_day(self):
        items = three_tasks()
        fake = DoneAwareMarvin(today=items, upcoming={TODAY: items})
        out = self.listing(marvin.cmd_today, fake, date=None, all=False, snoozed=False, subtasks=False)
        self.assert_unique_rows(out, [NELSON, ACCESS, OTHER])
        self.assertIn("(605a9bf663c9e42e1074976da8e3b)", out)
        self.assertIn("(605a9bf663c9e42e1074976da8e3c)", out)
        self.assertIn("(605a9bf6f)", out)
        out = self.listing(marvin.cmd_day, fake, date=["today"], incomplete=False, all=False,
                           snoozed=False, subtasks=False)
        self.assert_unique_rows(out, [NELSON, ACCESS, OTHER])

    def test_today_with_child_and_embedded_subtask(self):
        sub_id = "605a9bf663c9e42e1074976da8e3d000"
        parent = task(ACCESS, ACCESS_TITLE, TODAY,
                      subtasks={"k1": {"_id": sub_id, "title": "Embedded step", "done": False}})
        child = task(NELSON, NELSON_TITLE, TODAY, parentId=ACCESS)
        fake = DoneAwareMarvin(today=[parent, child], upcoming={TODAY: [parent, child]})
        out = self.listing(marvin.cmd_today, fake, date=None, all=False, snoozed=False, subtasks=False)
        self.assert_unique_rows(out, [NELSON, ACCESS, sub_id])

    def test_due(self):
        fake = DoneAwareMarvin(due=three_tasks(dueDate=TODAY))
        out = self.listing(marvin.cmd_due, fake)
        self.assert_unique_rows(out, [NELSON, ACCESS, OTHER])

    def test_completed_across_days(self):
        # The two tasks land on different days; ids must still differ.
        fake = DoneAwareMarvin(done={
            TODAY: [task(NELSON, NELSON_TITLE, TODAY, done=True)],
            YESTERDAY: [task(ACCESS, ACCESS_TITLE, YESTERDAY, done=True)],
        })
        out = self.listing(marvin.cmd_completed, fake, days=2)
        self.assert_unique_rows(out, [NELSON, ACCESS])

    def test_search(self):
        items = three_tasks()
        with mock.patch.object(marvin, "search_pool", lambda include_done=False: (items, [], True)):
            out = self.listing(marvin.cmd_search, DoneAwareMarvin(), query=["a"], all=False)
        self.assert_unique_rows(out, [NELSON, ACCESS, OTHER])

    def test_backlog(self):
        items = [task(NELSON, NELSON_TITLE), task(ACCESS, ACCESS_TITLE), task(OTHER, "Unrelated")]
        groups = [("Work", items[:1]), ("Security", items[1:])]
        with mock.patch.object(marvin, "gather_backlog", lambda uncategorized=False: (groups, [])):
            out = self.listing(marvin.cmd_backlog, DoneAwareMarvin(), uncategorized=False, subtasks=False)
        self.assert_unique_rows(out, [NELSON, ACCESS, OTHER])

    def test_projects_and_labels(self):
        cats = [{"_id": NELSON, "title": "Work"}, {"_id": ACCESS, "title": "Security"},
                {"_id": OTHER, "title": "Home"}]
        out = self.listing(marvin.cmd_projects, DoneAwareMarvin(categories=cats))
        self.assert_unique_rows(out, [NELSON, ACCESS, OTHER])
        out = self.listing(marvin.cmd_labels, DoneAwareMarvin(labels=cats))
        self.assert_unique_rows(out, [NELSON, ACCESS, OTHER])

    def test_fallback_is_the_full_id(self):
        self.assertEqual(marvin.fallback_id(NELSON), NELSON)


class AmbiguityError(unittest.TestCase):
    """The error lists every candidate as '<full id>  <whole title>' on one line."""

    def setUp(self):
        items = [task(NELSON, NELSON_TITLE, TODAY), task(ACCESS, ACCESS_TITLE, TODAY)]
        self.fake = DoneAwareMarvin(today=items, upcoming={TODAY: items})

    def done(self, ref):
        with mock.patch.object(marvin, "api_request", self.fake), \
                mock.patch.object(marvin, "sync_all_items", lambda *a, **k: None):
            return run(marvin.cmd_done, task_id=ref)

    def test_lists_every_match_with_full_id_and_title(self):
        code, out = self.done("605a9bf6")
        self.assertEqual(code, 1)
        self.assertEqual(self.fake.posts, [])
        self.assertIn(f"  {NELSON}  {NELSON_TITLE}", out)
        self.assertIn(f"  {ACCESS}  {ACCESS_TITLE}", out)  # untruncated title
        self.assertIn("matches 2 tasks", out)
        self.assertIn("Nothing was changed", out)

    def test_no_id_line_without_its_title(self):
        # The incident's `| tail -3` showed a bare id; every id line now carries a title.
        _, out = self.done("605a9bf6")
        id_lines = [ln for ln in out.splitlines() if re.search(r"\b605a9bf6[0-9a-f]+", ln)]
        self.assertEqual(len(id_lines), 2, out)
        for line in id_lines:
            self.assertTrue(NELSON_TITLE in line or ACCESS_TITLE in line, line)
        # The last lines alone still pair an id with its title.
        self.assertTrue(any(ACCESS_TITLE in ln and ACCESS in ln for ln in out.splitlines()[-3:]))

    def test_full_id_completes_the_right_task(self):
        self.fake.docs[NELSON] = task(NELSON, NELSON_TITLE, TODAY)
        code, out = self.done(NELSON)
        self.assertIsNone(code, out)
        self.assertEqual(self.fake.posts, [("markDone", {"itemId": NELSON,
                                                         "timeZoneOffset": marvin.get_tz_offset()})])

    def test_reorder_uses_the_same_error(self):
        with mock.patch.object(marvin, "api_request", self.fake):
            code, out = run(marvin.cmd_reorder, task_ids=["605a9bf6"])
        self.assertEqual(code, 1)
        self.assertIn(f"  {ACCESS}  {ACCESS_TITLE}", out)
        self.assertEqual(self.fake.posts, [])

    def test_category_ambiguity_prints_full_ids(self):
        cats = [{"_id": NELSON, "title": "Work"}, {"_id": ACCESS, "title": "Security"}]
        with mock.patch.object(marvin, "api_request", DoneAwareMarvin(categories=cats)):
            out = run(lambda a: marvin.resolve_category("605a9bf6"))[1]
        self.assertIn(f"  {NELSON}  # Work", out)
        self.assertIn(f"  {ACCESS}  # Security", out)


class Undone(unittest.TestCase):
    """`marvin undone` reopens a completed task with the doc/update setters."""

    def setUp(self):
        self.access = task(ACCESS, ACCESS_TITLE, TODAY, done=True, doneAt=1)
        self.nelson = task(NELSON, NELSON_TITLE, TODAY)  # still open
        self.fake = DoneAwareMarvin(today=[self.nelson], done={TODAY: [self.access]},
                                    docs={ACCESS: self.access, NELSON: self.nelson})

    def undone(self, ref):
        with mock.patch.object(marvin, "api_request", self.fake):
            return run(marvin.cmd_undone, task_id=ref)

    def assert_reopened(self, item_id):
        self.assertEqual(len(self.fake.posts), 1)
        endpoint, data = self.fake.posts[0]
        self.assertEqual(endpoint, "doc/update")
        self.assertEqual(data["itemId"], item_id)
        setters = {s["key"]: s["val"] for s in data["setters"]}
        self.assertEqual(set(setters), {"done", "doneAt", "fieldUpdates.done", "updatedAt"})
        self.assertIs(setters["done"], False)
        self.assertIsNone(setters["doneAt"])
        self.assertIsInstance(setters["fieldUpdates.done"], int)
        self.assertEqual(setters["fieldUpdates.done"], setters["updatedAt"])

    def test_full_id(self):
        code, out = self.undone(ACCESS)
        self.assertIsNone(code, out)
        self.assert_reopened(ACCESS)
        self.assertIn(f"✓ Reopened: {ACCESS_TITLE}", out)

    def test_prefix_matches_only_completed_tasks(self):
        # The open Nelson task shares the prefix but is not a reopen candidate.
        code, out = self.undone("605a9bf6")
        self.assertIsNone(code, out)
        self.assert_reopened(ACCESS)

    def test_prefix_ambiguous_among_completed_is_refused(self):
        other = task(OTHER, "Unrelated task", YESTERDAY, done=True)
        self.fake.done[YESTERDAY] = [other]
        code, out = self.undone("605a9bf6")
        self.assertEqual(code, 1)
        self.assertEqual(self.fake.posts, [])
        self.assertIn(f"  {OTHER}  Unrelated task", out)

    def test_open_task_is_refused(self):
        code, out = self.undone(NELSON)
        self.assertEqual(code, 1)
        self.assertIn("is not done", out)
        self.assertEqual(self.fake.posts, [])

    def test_no_match(self):
        code, out = self.undone("deadbeef")
        self.assertEqual(code, 1)
        self.assertIn("No completed task found", out)
        self.assertEqual(self.fake.posts, [])

    def test_prefix_shared_with_embedded_subtask_is_refused(self):
        sub_id = "605a9bf663c9e42e1074976da8e3c999"
        self.access["subtasks"] = {"k1": {"_id": sub_id, "title": "Embedded step", "done": True}}
        code, out = self.undone("605a9bf663c9e42e1074976da8e3c")
        self.assertEqual(code, 1)
        self.assertEqual(self.fake.posts, [])
        self.assertIn(f"  {ACCESS}  {ACCESS_TITLE}", out)
        self.assertIn(f"  {sub_id}  Embedded step", out)

    def test_embedded_subtask_full_id_is_refused(self):
        sub_id = "605a9bf663c9e42e1074976da8e3c999"
        self.access["subtasks"] = {"k1": {"_id": sub_id, "title": "Embedded step", "done": True}}
        code, out = self.undone(sub_id)
        self.assertEqual(code, 1)
        self.assertEqual(self.fake.posts, [])
        self.assertIn("is a subtask inside", out)

    def test_category_is_refused(self):
        cat_id = "c" * 32
        self.fake.docs[cat_id] = {"_id": cat_id, "title": "Work", "type": "category", "done": True}
        code, out = self.undone(cat_id)
        self.assertEqual(code, 1)
        self.assertIn("is a category", out)
        self.assertEqual(self.fake.posts, [])

    def test_recurring_template_is_refused(self):
        tmpl_id = "628bcfd6-85e9-4925-bdac-019bcafd2560"
        self.fake.docs[tmpl_id] = {"_id": tmpl_id, "title": "Review PRs", "db": "RecurringTasks", "done": True}
        code, out = self.undone(tmpl_id)
        self.assertEqual(code, 1)
        self.assertIn("is the recurring task", out)
        self.assertEqual(self.fake.posts, [])

    def test_registered_on_the_cli(self):
        with mock.patch.object(sys, "argv", ["marvin", "undone", "--help"]):
            code, out = run(lambda a: marvin.main())
        self.assertEqual(code, 0)
        self.assertIn("task_id", out)


if __name__ == "__main__":
    unittest.main()

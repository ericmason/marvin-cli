#!/usr/bin/env python3
"""
marvin - A simple CLI for Amazing Marvin

Commands:
    marvin add "Task title +today #Category @label"
    marvin add "Task" -d next monday     # Add task scheduled for a date
    marvin add "Subtask" -p <parent_id>  # Add subtask to a parent task
    marvin list                     # List today's tasks (default)
    marvin list --today             # List today's tasks
    marvin list --completed         # List completed tasks from last 7 days
    marvin list --due               # List tasks due soon
    marvin list --projects          # List projects/categories
    marvin list --labels            # List labels
    marvin today                    # List today's tasks (with subtasks)
    marvin day last friday          # List tasks for a specific day
    marvin day yesterday -i         # Show incomplete tasks from yesterday
    marvin subtasks <task_id>       # List subtasks of a task (incl. unscheduled)
    marvin note <task_id>           # Print a task's note
    marvin note <task_id> "text"    # Set a task's note (--append to add to it)
    marvin rm <task_id>             # Delete a task (asks for confirmation)
    marvin search <query>           # Search tasks and subtasks by title
    marvin done <task_id>           # Mark task or subtask complete
    marvin completed                # List completed tasks from last 7 days
    marvin completed --days 14      # List completed tasks from last 14 days
    marvin reorder <id1> <id2> ...  # Reorder tasks
    marvin snooze <task_id> 4h      # Hide a task from today until a wake time
    marvin unsnooze <task_id>       # Clear a snooze
    marvin snoozed                  # List snoozed tasks with wake times
    marvin skip-overdue             # Dismiss overdue recurring task instances
    marvin projects                 # List projects/categories
    marvin category Priority        # List open tasks in a category/project
    marvin category Priority -a     # ...including completed
    marvin backlog                  # List open, unscheduled tasks (all categories)
    marvin backlog -u               # ...uncategorized root bucket only
    marvin today -S                 # ...also fetch each task's child tasks
    marvin labels                   # List labels
    marvin setup                    # Configure API token
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: requests library required. Install with: pip3 install requests")
    sys.exit(1)

CONFIG_DIR = Path.home() / ".config" / "marvin"
CONFIG_FILE = CONFIG_DIR / "config.json"
API_BASE = "https://serv.amazingmarvin.com/api"

WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def parse_date(date_input):
    """Parse natural language date or YYYY-MM-DD format.

    Supports:
        - YYYY-MM-DD format
        - today, yesterday, tomorrow
        - last/this/next <weekday>
        - N days ago
        - last week, this week

    Returns date string in YYYY-MM-DD format.
    """
    if not date_input:
        return datetime.now().strftime("%Y-%m-%d")

    date_input = date_input.lower().strip()
    today = datetime.now().date()

    # Already in YYYY-MM-DD format
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_input):
        return date_input

    # Simple keywords
    if date_input == "today":
        return today.strftime("%Y-%m-%d")
    if date_input == "yesterday":
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")
    if date_input == "tomorrow":
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    # N days ago
    match = re.match(r"(\d+)\s*days?\s*ago", date_input)
    if match:
        days = int(match.group(1))
        return (today - timedelta(days=days)).strftime("%Y-%m-%d")

    # last/this/next <weekday>
    match = re.match(r"(last|this|next)\s+(\w+)", date_input)
    if match:
        modifier, day_name = match.groups()
        if day_name in WEEKDAYS:
            target_weekday = WEEKDAYS[day_name]
            current_weekday = today.weekday()

            if modifier == "last":
                # Find the most recent occurrence of that weekday before today
                days_back = (current_weekday - target_weekday) % 7
                if days_back == 0:
                    days_back = 7  # "last monday" on monday means 7 days ago
                result = today - timedelta(days=days_back)
            elif modifier == "this":
                # This week's occurrence (could be past or future)
                days_diff = target_weekday - current_weekday
                result = today + timedelta(days=days_diff)
            else:  # next
                # Find the next occurrence of that weekday
                days_forward = (target_weekday - current_weekday) % 7
                if days_forward == 0:
                    days_forward = 7  # "next monday" on monday means 7 days from now
                result = today + timedelta(days=days_forward)

            return result.strftime("%Y-%m-%d")
        elif day_name == "week":
            if modifier == "last":
                # Start of last week (Monday)
                days_since_monday = today.weekday()
                result = today - timedelta(days=days_since_monday + 7)
            elif modifier == "this":
                # Start of this week (Monday)
                days_since_monday = today.weekday()
                result = today - timedelta(days=days_since_monday)
            else:  # next
                # Start of next week (Monday)
                days_until_monday = (7 - today.weekday()) % 7
                if days_until_monday == 0:
                    days_until_monday = 7
                result = today + timedelta(days=days_until_monday)
            return result.strftime("%Y-%m-%d")

    # Just a weekday name (interpret as "last <weekday>")
    if date_input in WEEKDAYS:
        target_weekday = WEEKDAYS[date_input]
        current_weekday = today.weekday()
        days_back = (current_weekday - target_weekday) % 7
        if days_back == 0:
            days_back = 7
        result = today - timedelta(days=days_back)
        return result.strftime("%Y-%m-%d")

    # Couldn't parse - return as-is and let the API handle it
    print(f"Warning: Could not parse date '{date_input}', using as-is")
    return date_input


def load_config():
    """Load configuration from file."""
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE) as f:
        return json.load(f)


def save_config(config):
    """Save configuration to file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    os.chmod(CONFIG_FILE, 0o600)  # Secure the file


def get_token(full_access=False):
    """Get API token from config."""
    config = load_config()
    key = "full_access_token" if full_access else "api_token"
    token = config.get(key)
    if not token:
        print(f"Error: No {'full access ' if full_access else ''}API token configured.")
        print("Run 'marvin setup' to configure your tokens.")
        sys.exit(1)
    return token


def api_request(method, endpoint, data=None, full_access=False, tolerant=False, max_retries=5):
    """Make an API request to Amazing Marvin.

    Retries with backoff on HTTP 429 (the API's "Too Many Requests" rate limit)
    and on 5xx responses (the API throws intermittent 500s).
    When tolerant=True, returns None on other HTTP errors instead of exiting —
    used by bulk operations where one item's failure shouldn't abort the run.
    """
    token = get_token(full_access)
    headers = {
        "Content-Type": "application/json",
        "X-Full-Access-Token" if full_access else "X-API-Token": token,
    }
    url = f"{API_BASE}/{endpoint}"

    for attempt in range(max_retries + 1):
        resp = None
        try:
            if method == "GET":
                resp = requests.get(url, headers=headers, params=data)
            else:
                resp = requests.post(url, headers=headers, json=data)
            resp.raise_for_status()
            if not resp.text or resp.text == "OK":
                return None
            return resp.json()
        except json.JSONDecodeError:
            # Some endpoints return non-JSON on success
            return None
        except requests.exceptions.HTTPError as e:
            status = getattr(resp, "status_code", None)
            if attempt < max_retries and (status == 429 or (status or 0) >= 500):
                time.sleep(3 * (attempt + 1) if status == 429 else 2 * (attempt + 1))
                continue
            if tolerant:
                return None
            print(f"API Error: {e}")
            if resp is not None and resp.text:
                print(f"Response: {resp.text}")
            sys.exit(1)
        except requests.exceptions.RequestException as e:
            if attempt < max_retries:
                time.sleep(2 * (attempt + 1))
                continue
            if tolerant:
                return None
            print(f"Request failed: {e}")
            sys.exit(1)


def cmd_setup(args):
    """Set up API tokens."""
    print("Amazing Marvin API Setup")
    print("=" * 40)
    print("\nGet your tokens at: https://app.amazingmarvin.com/pre?api")
    print()

    config = load_config()

    api_token = input("API Token (for creating tasks): ").strip()
    if api_token:
        config["api_token"] = api_token

    full_token = input("Full Access Token (for reading data, optional): ").strip()
    if full_token:
        config["full_access_token"] = full_token

    save_config(config)
    print(f"\nConfig saved to {CONFIG_FILE}")

    # Test the connection
    if config.get("api_token"):
        print("\nTesting connection...")
        try:
            api_request("POST", "test")
            print("✓ API token is valid!")
        except SystemExit:
            print("✗ API token test failed")


def matchable_id(raw_id):
    """The portion of an _id that a user-supplied task ref is matched against.

    Recurring instances have a compound '<YYYY-MM-DD>_<uuid>' _id; every instance
    of the same recurring task shares the uuid and differs only in the date, so
    refs are matched against the uuid part (and against the full id as well).
    """
    if not raw_id:
        return ""
    return raw_id.split("_", 1)[-1] if "_" in raw_id else raw_id


def ref_matches(item, task_ref):
    """True if task_ref is a prefix of the item's full id or its matchable form."""
    full_id = item.get("_id", "")
    return full_id.startswith(task_ref) or matchable_id(full_id).startswith(task_ref)


def exit_ambiguous(task_ref, matches, action=None, recurring=False):
    """Report an ambiguous task ref and exit rather than guessing which task.

    Marvin ids created in the same session share a long common prefix, so a short
    ref regularly matches several unrelated tasks (and a task's own children).
    Silently picking one is how the wrong task gets completed or moved, so this
    prints every candidate with an id long enough to tell them apart.

    recurring=True means the ref names a recurring task's shared uuid, which
    matches every instance of it, not only the ones listed.
    """
    uids = unique_ids(matches)
    if recurring:
        print(f"Ambiguous task ref '{task_ref}' — it names a recurring task, and "
              f"every instance of it shares that id. Instances found:")
    else:
        print(f"Ambiguous task ref '{task_ref}' — matches {len(matches)} tasks:")
    for m in matches:
        full_id = m.get("_id", "")
        # A shortest-unique prefix can't separate recurring instances (they share
        # one uuid), so those fall back to the full compound id.
        shown = uids.get(full_id, "")
        if sum(1 for o in matches if matchable_id(o.get("_id", "")).startswith(shown)) > 1:
            shown = full_id
        title = m.get("title", "Untitled")
        if len(title) > 60:
            title = title[:57] + "..."
        day = m.get("day", "")
        meta = f"  sched:{day}" if day and day != "unassigned" else ""
        due = m.get("dueDate", "")
        meta += f"  due:{due}" if due else ""
        print(f"  • {title}{meta}")
        print(f"      {shown}")
    verb = action or "Re-run"
    if recurring:
        print(f"\n{verb} with the dated id of the instance you mean "
              f"(<YYYY-MM-DD>_<uuid>, as shown above).")
    else:
        print(f"\n{verb} with one of the ids above (a longer prefix is enough).")
    sys.exit(1)


def find_task_by_ref(task_ref, items, probe_children=True):
    """Find a task by ID reference (full ID or a prefix of it).

    An item whose full id equals the ref wins outright. Otherwise a ref that
    matches more than one item is an error, never a guess. When a prefix matches
    exactly one task, that task's own children are checked too: Marvin often
    gives a child an id sharing ~30 characters with its parent, so the parent
    alone matching in the caller's item pool does not mean the ref is
    unambiguous.

    A ref that reaches a recurring instance only through its uuid part names
    every instance of that recurring task, including ones outside `items`, so it
    is refused even when only one instance is in view.

    Returns the matched item or exits with an error.
    """
    exact = [i for i in items if i.get("_id") == task_ref]
    if len(exact) == 1:
        return exact[0]

    matches = [i for i in items if ref_matches(i, task_ref)]

    if not matches:
        print(f"No task found matching '{task_ref}'")
        sys.exit(1)
    if any(matches_recurring_uuid_only(i, task_ref) for i in matches):
        exit_ambiguous(task_ref, matches, recurring=True)

    if len(matches) == 1 and probe_children:
        parent_id = matches[0].get("_id", "")
        seen = {parent_id}
        for child in fetch_children(parent_id):
            cid = child.get("_id", "")
            if cid not in seen and ref_matches(child, task_ref):
                seen.add(cid)
                matches.append(child)

    if len(matches) > 1:
        exit_ambiguous(task_ref, matches)
    return matches[0]


def is_recurring_instance(item):
    """True if the item's _id has the '<YYYY-MM-DD>_<uuid>' recurring form."""
    full_id = item.get("_id", "")
    return "_" in full_id and full_id != matchable_id(full_id)


def matches_recurring_uuid_only(item, task_ref):
    """True if task_ref reaches a recurring instance only through its uuid part.

    Every instance of a recurring task carries the same uuid, so such a ref is
    ambiguous by nature: the instances a caller can see are not all there are.
    """
    return is_recurring_instance(item) and not item.get("_id", "").startswith(task_ref)


def is_full_length_id(task_ref):
    """True if the ref is long enough to be a whole Marvin id, not a prefix."""
    return len(matchable_id(task_ref)) >= 32


def refuse_recurring_template(doc):
    """Exit if doc is a recurring task's template rather than a task.

    A recurring instance's uuid part is its template's id, so a doc lookup on
    that uuid returns the template; completing, moving, or deleting it would act
    on the whole series instead of one occurrence.
    """
    if doc.get("db") == "RecurringTasks":
        print(f"'{doc.get('_id', '')}' is the recurring task '{doc.get('title', 'Untitled')}', "
              f"not one occurrence of it.")
        print("Use the dated id of an instance (<YYYY-MM-DD>_<uuid>); "
              "'marvin day <date>' lists them.")
        sys.exit(1)
    return doc


def gather_items(*sources):
    """Combine multiple item lists, deduplicating by _id."""
    seen = set()
    result = []
    for source in sources:
        if not source:
            continue
        for item in source:
            item_id = item.get("_id")
            if item_id and item_id not in seen:
                seen.add(item_id)
                result.append(item)
    return result


def short_id(raw_id):
    """Extract a consistent 8-character short ID from a raw task/subtask ID.

    A recurring instance ('2026-03-31_<uuid>') gets its full id: every instance
    of the task shares the uuid, so no shortened form names just that instance.

    Prefer unique_ids()/display_ids() when showing a list: 8 characters is often
    not unique in this account, so a bare short id can't safely be pasted back.
    """
    if raw_id and raw_id != matchable_id(raw_id):
        return raw_id
    return raw_id[:8]


def doc_get(doc_id):
    """Fetch a single doc by exact id, or None if it doesn't exist.

    The API answers a missing/deleted doc with HTTP 200 and an error payload
    ({"error": "not_found"}), so a truthy response is not proof the doc exists.
    """
    doc = api_request("GET", "doc", {"id": doc_id}, full_access=True, tolerant=True)
    if not isinstance(doc, dict) or doc.get("error") or not doc.get("_id"):
        return None
    return doc


def fetch_children(parent_id):
    """Child tasks of a task/category via the children endpoint.

    This is the only way to see a child whose day is "unassigned" — such a task
    appears in no day-based list (todayItems/dueItems/doneItems). Note the
    endpoint omits *completed* children; there is no flag to include them.
    """
    if not parent_id:
        return []
    return api_request(
        "GET", "children", {"parentId": parent_id}, full_access=True, tolerant=True
    ) or []


def fetch_all_or_exit(fetch, keys, what):
    """Run fetch(key) for every key concurrently; exit if any fetch fails.

    fetch returns None on failure. Ref resolution needs every list: a list
    that silently comes back empty can hide the second match that makes a ref
    ambiguous, so a partial sweep is refused instead of acted on.
    """
    with ThreadPoolExecutor(max_workers=POOL_WORKERS) as pool:
        results = list(pool.map(fetch, keys))
    failed = [k for k, r in zip(keys, results) if r is None]
    if failed:
        print(f"Could not fetch {what} {', '.join(failed[:5])}"
              f"{' …' if len(failed) > 5 else ''} from the Marvin API.")
        print("Refusing to resolve a task ref against a partial list; retry, or pass the full id.")
        sys.exit(1)
    return gather_items(*results)


def day_task_pool(include_done=False, done_date=None):
    """The day-based item pool: today's items, due items, optionally today's done."""
    # todayItems with no date returns the server's notion of today, which a
    # timezone rollover can put a day off from the local date — fetch both.
    local_today = datetime.now().strftime("%Y-%m-%d")
    sources = [
        api_request("GET", "todayItems", full_access=True) or [],
        api_request("GET", "todayItems", {"date": local_today}, full_access=True) or [],
        api_request("GET", "dueItems", full_access=True) or [],
    ]
    if include_done:
        date_str = done_date or datetime.now().strftime("%Y-%m-%d")
        sources.append(
            api_request("GET", "doneItems", {"date": date_str}, full_access=True) or []
        )
    return gather_items(*sources)


UPCOMING_DAYS = 14
# Concurrent requests when sweeping days or categories; api_request backs off
# on 429, so this only needs to stay modest.
POOL_WORKERS = 4


def upcoming_task_pool(days=UPCOMING_DAYS):
    """Tasks scheduled on each of the next `days` days (tomorrow onward).

    A task scheduled for a future day appears in no list for today and is not
    returned as a child of its category, so it has to be fetched day by day.
    """
    today = datetime.now().date()
    dates = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, days + 1)]

    def fetch_day(date_str):
        return api_request(
            "GET", "todayItems", {"date": date_str}, full_access=True, tolerant=True
        )

    return fetch_all_or_exit(fetch_day, dates, "the day list for")


def in_resolve_window(date_str):
    """True if a prefix shown in date_str's listing can be resolved back.

    The day-based pools cover today through the next UPCOMING_DAYS days. A task
    on any other day is reached only through the category sweep, which misses
    some tasks (a past-day task can fail to resolve even though 'marvin day'
    listed it), so listings for those days show full ids, which resolve_task
    finds by exact doc lookup.
    """
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return False
    today = datetime.now().date()
    return today <= day <= today + timedelta(days=UPCOMING_DAYS)


def list_number(task_ref, items):
    """The 1-based list index task_ref names, or None if it is a task id.

    Marvin ids can be all digits ('27102153'), so only a short number that fits
    the list counts as a position; anything else is resolved as an id.
    """
    if task_ref.isdigit() and len(task_ref) <= 3 and 1 <= int(task_ref) <= len(items):
        return int(task_ref)
    return None


def deep_task_pool():
    """Every task filed directly under the root bucket or any category/project.

    Reaches unscheduled tasks, which no day-based list returns. Costs one request
    per category, so a full id skips it. Exits if any category fails to load.
    """
    cats = api_request("GET", "categories", full_access=True) or []
    parent_ids = ["unassigned"] + [c["_id"] for c in cats]

    def fetch(pid):
        return api_request(
            "GET", "children", {"parentId": pid}, full_access=True, tolerant=True
        )

    return fetch_all_or_exit(fetch, parent_ids, "the tasks under")


def resolve_task(task_ref, extra_items=None, include_done=True, allow_deep=True):
    """Resolve a task ref to exactly one item, or exit.

    A whole id resolves with one exact doc lookup. A prefix is matched against
    every pool at once — the day-based lists, the next UPCOMING_DAYS days, and
    every category's children — because a match in a narrow pool says nothing
    about matches in a wider one: stopping at the first hit is how a prefix
    meant for an unscheduled task deleted a scheduled one. Ambiguity is always
    an error.
    """
    if is_full_length_id(task_ref):
        doc = doc_get(task_ref)
        if doc:
            return refuse_recurring_template(doc)

    items = gather_items(day_task_pool(include_done=include_done), extra_items or [])
    if allow_deep:
        items = gather_items(items, upcoming_task_pool(), deep_task_pool())
    if any(ref_matches(i, task_ref) for i in items):
        return find_task_by_ref(task_ref, items)

    doc = None if is_full_length_id(task_ref) else doc_get(task_ref)
    if doc:
        return refuse_recurring_template(doc)

    print(f"No task found matching '{task_ref}'")
    print(f"Tried today/due/done lists, the next {UPCOMING_DAYS} days, every category, and an exact id lookup.")
    print("A task nested under another task resolves only by its FULL id —")
    print("run 'marvin subtasks <parent>' to get it. For a task on a past day,")
    print("'marvin day <date>' prints its full id.")
    sys.exit(1)


def format_task(item, uids=None):
    """Format a task for display. Returns (done_marker, title, time_str, id).

    Pass the map from display_ids()/unique_ids() so the shown id is unique within
    the list being printed and can therefore be pasted back into another command.
    """
    done = "✓" if item.get("done") else "○"
    title = item.get("title", "Untitled")
    raw_id = item.get("_id", "")
    sid = (uids or {}).get(raw_id) or short_id(raw_id)
    time_est = item.get("timeEstimate")
    time_str = f" [{time_est // 60000}m]" if time_est else ""
    return done, title, time_str, sid


def unique_ids(items):
    """Map each item's _id to the shortest prefix (>= 8 chars) that is unique
    among `items`, computed over the same matchable form find_task_by_ref uses
    (the part after '_' for recurring instances).

    Marvin ids for bulk-created tasks share a long common prefix and differ only
    at the end, so a fixed 8-char id collides. Displaying a context-unique prefix
    keeps the shown id both readable and usable as a task ref. Genuine duplicate
    ids fall back to the full form.

    A recurring instance is always shown by its full '<YYYY-MM-DD>_<uuid>' id.
    Its uuid is shared with every other instance of the task, in this list or
    not, so any shortened form would name all of them.
    """
    forms = [matchable_id(i.get("_id", "")) for i in items]
    out = {}
    for item, form in zip(items, forms):
        raw_id = item.get("_id", "")
        if is_recurring_instance(item):
            out[raw_id] = raw_id
            continue
        n = 8
        while n < len(form) and sum(1 for f in forms if f.startswith(form[:n])) > 1:
            n += 1
        out[raw_id] = form[:max(8, n)]
    return out


def nested_child_ids(items):
    """Map nested child tasks to their FULL id for display.

    A task nested under another task appears in no day-based list and is not a
    direct child of any category, so ref resolution can only find it through an
    exact doc lookup — a shortened prefix would not resolve. Showing the full id
    keeps these listings copy-pasteable into done/move/note/rm.
    """
    return {i.get("_id", ""): i.get("_id", "") for i in items}


def display_ids(*groups):
    """unique_ids() over several item lists plus every embedded subtask in them.

    Embedded subtasks are shown alongside real tasks, so their ids have to be
    disambiguated against the same pool — otherwise a displayed id could be
    unique among tasks yet still collide with a subtask id in the same output.
    """
    pool = []
    for group in groups:
        for item in group or []:
            pool.append(item)
            pool.extend((item.get("subtasks") or {}).values())
    return unique_ids(gather_items(pool))


def cmd_add(args):
    """Add a new task."""
    title = " ".join(args.title)
    if not title:
        print("Error: Task title required")
        sys.exit(1)

    data = {"title": title, "timeZoneOffset": get_tz_offset()}

    if args.day:
        day_str = parse_date(" ".join(args.day))
        data["day"] = day_str

    parent_title = None
    if args.parent:
        # resolve_task also reaches unscheduled tasks, so a subtask can be added
        # under a backlog parent, not just one on today's list.
        parent = resolve_task(args.parent)
        data["parentId"] = parent["_id"]
        parent_title = parent.get("title", "Untitled")

    result = api_request("POST", "addTask", data)

    if result:
        task_id = result.get("_id", result.get("id", "unknown"))
        print(f"✓ Added: {title}")
        if parent_title:
            print(f"  Parent: {parent_title}")
        if args.day:
            print(f"  Scheduled: {day_str}")
        print(f"  ID: {task_id}")
    else:
        print(f"✓ Added: {title}")
        if parent_title:
            print(f"  Parent: {parent_title}")
        if args.day:
            print(f"  Scheduled: {day_str}")


def collect_nested_children(items, exclude_ids=(), include_done=False):
    """Fetch child tasks for each item: {parent _id: [children]}.

    Costs one request per item, which is why every caller puts this behind an
    opt-in flag. Children already present in the caller's list are excluded so
    nothing is printed twice.
    """
    exclude = set(exclude_ids)
    out = {}
    for item in items:
        parent_id = item.get("_id")
        kids = [c for c in fetch_children(parent_id) if c.get("_id") not in exclude]
        if not include_done:
            kids = [c for c in kids if not c.get("done")]
        if kids:
            out[parent_id] = sorted(kids, key=lambda x: x.get("rank", 999999))
    return out


def cmd_today(args):
    """List today's tasks."""
    date_str = parse_date(args.date) if args.date else datetime.now().strftime("%Y-%m-%d")
    show_tasks_for_date(date_str, include_completed=args.all,
                        show_snoozed=getattr(args, "snoozed", False),
                        show_children=getattr(args, "subtasks", False))


def cmd_day(args):
    """List tasks for a specific day."""
    date_input = " ".join(args.date) if args.date else "today"
    date_str = parse_date(date_input)
    show_tasks_for_date(date_str, show_incomplete_only=args.incomplete, include_completed=args.all,
                        show_snoozed=getattr(args, "snoozed", False),
                        show_children=getattr(args, "subtasks", False))


def show_tasks_for_date(date_str, show_incomplete_only=False, include_completed=False,
                        show_snoozed=False, show_children=False):
    """Display tasks for a given date."""
    today_items = api_request("GET", "todayItems", {"date": date_str}, full_access=True) or []

    if include_completed:
        done_items = api_request("GET", "doneItems", {"date": date_str}, full_access=True) or []
        items = gather_items(today_items, done_items)
    else:
        items = today_items

    # The Marvin app hides snoozed tasks everywhere except the master list, so
    # hide them here too (with a count) unless --snoozed asks to show them.
    now = datetime.now()
    snoozed_wakes = {}
    for item in items:
        wake = snoozed_until(item, now)
        if wake and not item.get("done"):
            snoozed_wakes[item.get("_id")] = wake
    hidden_count = 0
    if not show_snoozed and snoozed_wakes:
        hidden_count = len(snoozed_wakes)
        items = [i for i in items if i.get("_id") not in snoozed_wakes]

    if not items:
        if hidden_count:
            print(f"No tasks visible for {date_str}.")
            print(f"  💤 {hidden_count} snoozed — 'marvin snoozed' to list, --snoozed to show")
        else:
            print(f"No tasks scheduled for {date_str}.")
        return

    if show_incomplete_only:
        items = [i for i in items if not i.get("done")]
        if not items:
            print(f"No incomplete tasks for {date_str}.")
            return

    # Build parent-child relationships
    items_by_id = {item.get("_id"): item for item in items}
    children_by_parent = {}
    top_level = []

    for item in items:
        parent_id = item.get("parentId")
        if parent_id and parent_id in items_by_id:
            if parent_id not in children_by_parent:
                children_by_parent[parent_id] = []
            children_by_parent[parent_id].append(item)
        else:
            top_level.append(item)

    # Sort by rank (lower = higher priority)
    top_level = sorted(top_level, key=lambda x: x.get("rank", 999999))

    # Unscheduled children live in no day-based list, so they can only be reached
    # through the children endpoint — one request per top-level task, hence opt-in.
    if show_children:
        fetched = collect_nested_children(
            top_level, exclude_ids=items_by_id.keys(), include_done=include_completed
        )
        for parent_id, kids in fetched.items():
            children_by_parent[parent_id] = children_by_parent.get(parent_id, []) + kids

    for parent_id in children_by_parent:
        children_by_parent[parent_id] = sorted(
            children_by_parent[parent_id], key=lambda x: x.get("rank", 999999)
        )

    def snooze_note(item):
        wake = snoozed_wakes.get(item.get("_id"))
        return f" 💤 until {format_wake(wake, now)}" if wake else ""

    uids = display_ids(items, [c for kids in children_by_parent.values() for c in kids])
    if not in_resolve_window(date_str):
        uids = {raw_id: raw_id for raw_id in uids}
    if show_children:
        uids.update(nested_child_ids([c for kids in fetched.values() for c in kids]))

    print(f"Tasks for {date_str}:")
    print("-" * 40)

    for idx, item in enumerate(top_level, 1):
        done, title, time_str, sid = format_task(item, uids)
        print(f"  {idx}. {done} {title}{time_str}{snooze_note(item)}  ({sid})")

        # Print child items (separate tasks with parentId)
        item_id = item.get("_id")
        if item_id in children_by_parent:
            for subtask in children_by_parent[item_id]:
                done, title, time_str, sid = format_task(subtask, uids)
                print(f"      {done} {title}{time_str}{snooze_note(subtask)}  ({sid})")

        # Print embedded subtasks
        embedded_subtasks = item.get("subtasks", {})
        if embedded_subtasks:
            # Sort by rank
            sorted_subtasks = sorted(
                embedded_subtasks.values(),
                key=lambda x: x.get("rank", 999999)
            )
            for subtask in sorted_subtasks:
                sub_done = "✓" if subtask.get("done") else "○"
                sub_title = subtask.get("title", "Untitled")
                raw = subtask.get("_id", "")
                sub_sid = uids.get(raw) or short_id(raw)
                print(f"      {sub_done} {sub_title}  ({sub_sid})")

    if hidden_count:
        print(f"\n  💤 {hidden_count} snoozed — 'marvin snoozed' to list, --snoozed to show")


def find_embedded_subtasks(task_ref, items):
    """Every embedded subtask within items whose id or key starts with task_ref.

    Returns a list of (parent_item, subtask_key, subtask_dict).
    """
    found = []
    for item in items:
        embedded = item.get("subtasks", {})
        if not embedded:
            continue
        for key, sub in embedded.items():
            sub_id = sub.get("_id", "")
            if sub_id.startswith(task_ref) or key.startswith(task_ref):
                found.append((item, key, sub))
    return found


def mark_done(item):
    """Mark a task or project done.

    markDone only accepts tasks ("Can only mark Tasks done with this API"), so a
    project is completed with a doc update instead, setting the same fields the
    app sets.
    """
    if item.get("type") == "category":
        print(f"'{item.get('title', 'Untitled')}' is a category; only tasks and projects can be done.")
        sys.exit(1)
    if item.get("type") == "project":
        now = int(datetime.now().timestamp() * 1000)
        api_request("POST", "doc/update", {
            "itemId": item["_id"],
            "setters": [
                {"key": "done", "val": True},
                {"key": "doneAt", "val": now},
                {"key": "fieldUpdates.done", "val": now},
                {"key": "updatedAt", "val": now},
            ],
        }, full_access=True)
        print(f"✓ Marked done: {item.get('title', 'Untitled')} (project)")
        return
    api_request("POST", "markDone", {"itemId": item["_id"], "timeZoneOffset": get_tz_offset()})
    print(f"✓ Marked done: {item.get('title', 'Untitled')}")


def cmd_done(args):
    """Mark a task or subtask as done."""
    task_ref = args.task_id
    today_items = api_request("GET", "todayItems", full_access=True) or []
    # Also check completed items so we can find subtasks of done parents
    date_str = datetime.now().strftime("%Y-%m-%d")
    done_items = api_request("GET", "doneItems", {"date": date_str}, full_access=True) or []
    # Also check due items so we can complete overdue/recurring tasks
    due_items = api_request("GET", "dueItems", full_access=True) or []
    items = gather_items(today_items, done_items, due_items)

    # A short number that fits the list is a list index for top-level tasks
    idx = list_number(task_ref, items)
    if idx:
        mark_done(items[idx - 1])
        return

    # Embedded subtasks live inside their parent's doc, so they are searched in
    # the same swept pool as tasks. With none matching, the ref is a task (or
    # project) ref and resolve_task refuses it if it is ambiguous.
    pool = items if is_full_length_id(task_ref) else gather_items(
        items, upcoming_task_pool(), deep_task_pool())
    subs = find_embedded_subtasks(task_ref, pool)
    if not subs:
        mark_done(resolve_task(task_ref, extra_items=pool, allow_deep=False))
        return

    task_hits = [i for i in pool if ref_matches(i, task_ref)]
    if len(subs) > 1 or task_hits:
        exit_ambiguous(task_ref, task_hits + [sub for _, _, sub in subs],
                       action="marvin done")
    parent, sub_key, subtask = subs[0]
    if parent:
        now = int(datetime.now().timestamp() * 1000)
        data = {
            "itemId": parent["_id"],
            "setters": [
                {"key": f"subtasks.{sub_key}.done", "val": True},
                {"key": f"subtasks.{sub_key}.completedAt", "val": now},
                {"key": "updatedAt", "val": now},
            ],
        }
        api_request("POST", "doc/update", data, full_access=True)
        print(f"✓ Marked done: {subtask.get('title', 'Untitled')}")
        print(f"  (subtask of: {parent.get('title', 'Untitled')})")
        return

    print(f"No task or subtask found matching '{task_ref}'")
    sys.exit(1)


def cmd_completed(args):
    """List completed tasks from the last N days."""
    days = args.days
    today = datetime.now().date()

    all_completed = []

    # Fetch completed tasks for each day using the doneItems endpoint
    for i in range(days):
        current_date = today - timedelta(days=i)
        date_str = current_date.strftime("%Y-%m-%d")
        items = api_request("GET", "doneItems", {"date": date_str}, full_access=True)

        if items:
            for item in items:
                item["_completed_date"] = date_str
                all_completed.append(item)

    if not all_completed:
        print(f"No completed tasks in the last {days} days.")
        return

    # Group by date
    by_date = {}
    for item in all_completed:
        date = item.get("_completed_date", "unknown")
        if date not in by_date:
            by_date[date] = []
        by_date[date].append(item)

    print(f"Completed tasks (last {days} days):")
    print("=" * 50)

    uids = display_ids(all_completed)

    total = 0
    for date in sorted(by_date.keys(), reverse=True):
        items = by_date[date]
        total += len(items)

        # Format date nicely
        try:
            dt = datetime.strptime(date, "%Y-%m-%d").date()
            if dt == today:
                date_label = f"{date} (today)"
            elif dt == today - timedelta(days=1):
                date_label = f"{date} (yesterday)"
            else:
                date_label = f"{date} ({dt.strftime('%A')})"
        except ValueError:
            date_label = date

        print(f"\n{date_label}:")
        print("-" * 40)

        for item in items:
            _, title, time_str, sid = format_task(item, uids)
            print(f"  ✓ {title}{time_str}  ({sid})")
            # Show completed embedded subtasks
            for sub in sorted(
                item.get("subtasks", {}).values(),
                key=lambda x: x.get("rank", 999999),
            ):
                if sub.get("done"):
                    sub_title = sub.get("title", "Untitled")
                    sub_sid = uids.get(sub.get("_id", "")) or short_id(sub.get("_id", ""))
                    print(f"      ✓ {sub_title}  ({sub_sid})")
                    total += 1

    print(f"\n{total} tasks completed in {days} days")


DONE_CHILD_SCAN_DAYS = 14
DONE_CHILD_LOOKAHEAD_DAYS = 2


def cmd_subtasks(args):
    """List subtasks of a specific task.

    Marvin has two distinct things called subtasks and both are shown:
      * child tasks   — separate task docs whose parentId points at this task
      * embedded subtasks — lightweight entries inside the parent's `subtasks` field

    Child tasks are read from the children endpoint rather than inferred from the
    day-based lists. A freshly added child has day "unassigned", so it appears in
    none of todayItems/dueItems/doneItems and was previously invisible here.
    """
    task_ref = args.task_id

    parent = resolve_task(task_ref)
    parent_title = parent.get("title", "Untitled")
    parent_id = parent.get("_id")

    children = fetch_children(parent_id)

    # The children endpoint omits completed children, so -a backfills them from
    # the done lists. Anything completed longer ago than the scan window stays
    # out of reach; say so rather than implying the list is complete.
    done_scan_limited = False
    if args.all:
        done_sources = []
        today = datetime.now().date()
        # Completing an unscheduled task stamps its `day` with the completion
        # date, which timezone offsets can push a day ahead of local today — so
        # the window reaches forward as well as back.
        for i in range(-DONE_CHILD_LOOKAHEAD_DAYS, DONE_CHILD_SCAN_DAYS):
            date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            done_sources.append(
                api_request("GET", "doneItems", {"date": date_str},
                            full_access=True, tolerant=True) or []
            )
        done_children = [
            it for it in gather_items(*done_sources)
            if it.get("parentId") == parent_id
        ]
        children = gather_items(children, done_children)
        done_scan_limited = True
    else:
        children = [c for c in children if not c.get("done")]

    children = sorted(children, key=lambda x: x.get("rank", 999999))

    embedded = list((parent.get("subtasks") or {}).values())
    hidden_embedded = 0
    if not args.all:
        open_embedded = [s for s in embedded if not s.get("done")]
        hidden_embedded = len(embedded) - len(open_embedded)
        embedded = open_embedded
    embedded = sorted(embedded, key=lambda x: x.get("rank", 999999))

    if not children and not embedded:
        if hidden_embedded:
            print(f"No open subtasks for: {parent_title}")
            print(f"  ({hidden_embedded} completed — use --all to show)")
        else:
            print(f"No subtasks for: {parent_title}")
        return

    print(f"Subtasks of: {parent_title}")
    print("-" * 40)

    uids = display_ids(children, [parent])
    uids.update(nested_child_ids(children))
    for child in children:
        done, title, time_str, sid = format_task(child, uids)
        day = child.get("day", "")
        meta = f"  sched:{day}" if day and day != "unassigned" else ""
        due = child.get("dueDate", "")
        meta += f"  due:{due}" if due else ""
        print(f"  {done} {title}{time_str}{meta}  ({sid})")

    for sub in embedded:
        sub_done = "✓" if sub.get("done") else "○"
        sub_title = sub.get("title", "Untitled")
        sub_sid = uids.get(sub.get("_id", "")) or short_id(sub.get("_id", ""))
        print(f"  {sub_done} {sub_title}  ({sub_sid})")

    total = len(children) + len(embedded)
    print(f"\n{total} subtask(s)")
    if hidden_embedded:
        print(f"  ({hidden_embedded} completed hidden — use --all to show)")
    if done_scan_limited:
        print(f"  (completed children found by scanning {DONE_CHILD_SCAN_DAYS} days "
              f"either side of today; older completions aren't listed)")


def cmd_note(args):
    """Read or write a task's note field."""
    text = " ".join(args.text) if args.text else None
    item = resolve_task(args.task_id)
    title = item.get("title", "Untitled")

    # The resolved item may come from a list endpoint that omits `note`; read the
    # doc so appending never truncates an existing note.
    doc = doc_get(item["_id"]) or item
    current = doc.get("note") or ""

    if text is None:
        if not current:
            print(f"No note on: {title}")
            return
        print(f"Note on: {title}")
        print("-" * 40)
        print(current)
        return

    if args.append and current:
        new_note = current.rstrip("\n") + "\n" + text
    else:
        new_note = text

    now = int(datetime.now().timestamp() * 1000)
    api_request("POST", "doc/update", {
        "itemId": item["_id"],
        "setters": [
            {"key": "note", "val": new_note},
            {"key": "fieldUpdates.note", "val": now},
            {"key": "updatedAt", "val": now},
        ],
    }, full_access=True)
    verb = "Appended to" if (args.append and current) else "Set"
    print(f"✓ {verb} note on '{title[:50]}'")


def cmd_rm(args):
    """Delete a task permanently, after confirmation."""
    item = resolve_task(args.task_id)
    title = item.get("title", "Untitled")

    # doc/delete needs the current _rev as itemVersion; list endpoints carry it,
    # but re-read to be sure it is current.
    doc = doc_get(item["_id"])
    if not doc:
        print(f"Task no longer exists: {title}")
        sys.exit(1)

    children = fetch_children(doc["_id"])
    print(f"Delete: {title}")
    day = doc.get("day", "")
    if day and day != "unassigned":
        print(f"  scheduled: {day}")
    if children:
        print(f"  ⚠ has {len(children)} open child task(s) — they are NOT deleted "
              f"and will be left orphaned:")
        for c in children[:5]:
            print(f"      - {c.get('title', 'Untitled')[:60]}")
        if len(children) > 5:
            print(f"      … and {len(children) - 5} more")

    if not args.force:
        if not sys.stdin.isatty():
            print("\nRefusing to delete without confirmation. Re-run with --force.")
            sys.exit(1)
        answer = input("\nThis cannot be undone. Type 'yes' to delete: ").strip().lower()
        if answer != "yes":
            print("Aborted — nothing deleted.")
            return

    api_request("POST", "doc/delete", {
        "itemId": doc["_id"],
        "itemVersion": doc.get("_rev"),
    }, full_access=True)

    if doc_get(doc["_id"]):
        print(f"✗ Delete did not take effect for '{title[:50]}'")
        sys.exit(1)
    print(f"✓ Deleted: {title[:60]}")


def cmd_projects(args):
    """List categories/projects."""
    items = api_request("GET", "categories", full_access=True)

    if not items:
        print("No categories found.")
        return

    print("Categories/Projects:")
    print("-" * 40)

    for item in items:
        title = item.get("title", "Untitled")
        cat_id = item.get("_id", "")[:8]
        print(f"  # {title}  ({cat_id})")


def cmd_labels(args):
    """List labels."""
    items = api_request("GET", "labels", full_access=True)

    if not items:
        print("No labels found.")
        return

    print("Labels:")
    print("-" * 40)

    for item in items:
        title = item.get("title", "Untitled")
        label_id = item.get("_id", "")[:8]
        print(f"  @ {title}  ({label_id})")


def resolve_category(ref):
    """Resolve a category/project by name or 8-char id prefix.

    Matches, in priority order: exact (case-insensitive) title, then id prefix,
    then title substring. Returns the matched category dict, or None after
    printing guidance when there is no match or the match is ambiguous.
    """
    cats = api_request("GET", "categories", full_access=True) or []
    ref_l = ref.lower()
    exact = [c for c in cats if c.get("title", "").lower() == ref_l]
    if len(exact) == 1:
        return exact[0]
    byid = [c for c in cats if c.get("_id", "").startswith(ref)]
    if len(byid) == 1:
        return byid[0]
    subs = [c for c in cats if ref_l in c.get("title", "").lower()]
    if len(subs) == 1:
        return subs[0]
    matches = exact or byid or subs
    if not matches:
        print(f"No category/project matching '{ref}'. Run 'marvin projects' to list them.")
        return None
    print(f"Ambiguous '{ref}' — matches {len(matches)}:")
    for c in matches:
        print(f"  # {c.get('title', 'Untitled')}  ({c.get('_id', '')[:8]})")
    return None


def cmd_category(args):
    """List tasks filed in a category/project (by name or id)."""
    ref = " ".join(args.name).strip()
    if not ref:
        print("Error: category name or id required")
        sys.exit(1)
    cat = resolve_category(ref)
    if not cat:
        return

    items = api_request("GET", "children", {"parentId": cat["_id"]}, full_access=True) or []
    tasks = [it for it in items if args.all or not it.get("done")]
    # Unscheduled first, then by scheduled day ascending.
    tasks.sort(key=lambda it: (it.get("day", "") not in ("", "unassigned"), it.get("day", "")))

    # Tasks filed in a category can themselves have child tasks, which the
    # category listing never returns. -S walks one level down into each.
    nested = collect_nested_children(
        tasks, exclude_ids=[i.get("_id") for i in items], include_done=args.all
    ) if args.subtasks else {}

    title = cat.get("title", "Untitled")
    print(f"# {title}  ({len(tasks)} {'task' if len(tasks) == 1 else 'tasks'})")
    print("-" * 40)
    if not tasks:
        print("  (no tasks)")
    uids = display_ids(tasks, [c for kids in nested.values() for c in kids])
    uids.update(nested_child_ids([c for kids in nested.values() for c in kids]))

    def meta_for(it):
        day = it.get("day", "")
        due = it.get("dueDate", "")
        meta = f"  sched:{day}" if day and day != "unassigned" else ""
        return meta + (f"  due:{due}" if due else "")

    for it in tasks:
        done, ttl, time_str, sid = format_task(it, uids)
        print(f"  {done} {ttl}{time_str}{meta_for(it)}  ({sid})")
        for kid in nested.get(it.get("_id"), []):
            kdone, kttl, ktime, ksid = format_task(kid, uids)
            print(f"      {kdone} {kttl}{ktime}{meta_for(kid)}  ({ksid})")
    if len(items) >= 200:
        print("  … (200-item API cap hit — list may be truncated)")


def cmd_backlog(args):
    """List open, unscheduled tasks (day == unassigned) so nothing gets forgotten.

    An unscheduled task lives under whatever category it is filed in (or the root
    'unassigned' bucket if uncategorized), so this walks the root plus every
    category/project and collects everything with no scheduled day.
    """
    cats = api_request("GET", "categories", full_access=True) or []
    parents = [("(uncategorized)", "unassigned")]
    if not args.uncategorized:
        parents += [(c.get("title", "Untitled"), c["_id"]) for c in cats]

    groups = []       # (label, [tasks])
    truncated = []    # parents that hit the 200-item cap
    for label, pid in parents:
        items = api_request("GET", "children", {"parentId": pid}, full_access=True, tolerant=True) or []
        if len(items) >= 200:
            truncated.append(label)
        open_unsched = [
            it for it in items
            if it.get("day", "unassigned") == "unassigned" and not it.get("done")
        ]
        if open_unsched:
            groups.append((label, open_unsched))

    total = sum(len(t) for _, t in groups)
    if not total:
        print("No unscheduled tasks. Backlog is clear.")
        return

    all_listed = [it for _, tasks in groups for it in tasks]
    # -S walks one level down into each listed task. Note this only reaches
    # subtasks of tasks that are themselves unscheduled — an unscheduled subtask
    # of a *scheduled* parent is found with 'marvin subtasks <parent>'.
    nested = collect_nested_children(all_listed) if args.subtasks else {}

    # Unique ids computed across the whole backlog so a copied id is unambiguous.
    uids = display_ids(all_listed, [c for kids in nested.values() for c in kids])
    uids.update(nested_child_ids([c for kids in nested.values() for c in kids]))
    print(f"Unscheduled backlog — {total} open task(s):")
    for label, tasks in groups:
        print(f"\n# {label}  ({len(tasks)})")
        for it in tasks:
            done, ttl, time_str, sid = format_task(it, uids)
            due = it.get("dueDate", "")
            due_s = f"  due:{due}" if due else ""
            print(f"  {done} {ttl}{time_str}{due_s}  ({sid})")
            for kid in nested.get(it.get("_id"), []):
                kdone, kttl, ktime, ksid = format_task(kid, uids)
                kdue = kid.get("dueDate", "")
                print(f"      {kdone} {kttl}{ktime}"
                      f"{f'  due:{kdue}' if kdue else ''}  ({ksid})")
    if truncated:
        print(f"\n  ⚠ 200-item API cap hit for: {', '.join(truncated)} — some tasks may be hidden.")


def cmd_due(args):
    """List tasks due soon."""
    items = api_request("GET", "dueItems", full_access=True)

    if not items:
        print("No due tasks.")
        return

    print("Due tasks:")
    print("-" * 40)

    uids = display_ids(items)
    for item in items:
        _, title, time_str, sid = format_task(item, uids)
        due = item.get("dueDate", "no date")
        print(f"  ! {title}{time_str}  (due: {due}) ({sid})")


def cmd_estimate(args):
    """Set time estimate for a task."""
    task_ref = args.task_id
    minutes = args.minutes

    item = resolve_task(task_ref)
    task_id = item["_id"]
    title = item.get("title", "Untitled")

    # Update the task's time estimate (stored in milliseconds)
    now = int(datetime.now().timestamp() * 1000)
    time_ms = minutes * 60000
    data = {
        "itemId": task_id,
        "setters": [
            {"key": "timeEstimate", "val": time_ms},
            {"key": "fieldUpdates.timeEstimate", "val": now},
            {"key": "updatedAt", "val": now},
        ],
    }
    api_request("POST", "doc/update", data, full_access=True)
    print(f"✓ Set estimate for '{title[:50]}' to {minutes}m")


def cmd_move(args):
    """Move/reschedule a task to a different day."""
    task_ref = args.task_id
    target_date = parse_date(" ".join(args.date)) if args.date else datetime.now().strftime("%Y-%m-%d")

    # resolve_task already covers today, due, and the upcoming days; --from adds
    # a day outside that window (such as a past day).
    extra = []
    if args.from_date:
        source_date = parse_date(args.from_date)
        extra = api_request("GET", "todayItems", {"date": source_date}, full_access=True) or []

    item = resolve_task(task_ref, extra_items=extra)
    task_id = item["_id"]
    title = item.get("title", "Untitled")

    # Update the task's scheduled day
    now = int(datetime.now().timestamp() * 1000)
    data = {
        "itemId": task_id,
        "setters": [
            {"key": "day", "val": target_date},
            {"key": "fieldUpdates.day", "val": now},
            {"key": "updatedAt", "val": now},
        ],
    }
    api_request("POST", "doc/update", data, full_access=True)
    print(f"✓ Moved '{title[:50]}' to {target_date}")


DURATION_TOKEN = r"(\d+)\s*(d(?:ays?)?|h(?:rs?|ours?)?|m(?:ins?|inutes?)?|s(?:ecs?|econds?)?)"


def parse_snooze_until(text, now=None):
    """Parse a snooze duration or wake time into a local datetime.

    Durations:  4h, 30m, 90min, 2h30m, 2d, 45s, "4 hours", "1 hour 30 minutes"
    Wake times: 9am, 4:15pm, 16:30, noon, midnight, tomorrow, "tomorrow 9am"
                (a bare clock time already past today rolls to tomorrow)

    Returns a datetime, or None if the text can't be parsed.
    """
    now = now or datetime.now()
    t = text.lower().strip()
    t = re.sub(r"^(until|till|for|in)\s+", "", t)

    # Duration form: one or more <number><unit> tokens
    if re.fullmatch(rf"(?:{DURATION_TOKEN}[\s,]*)+", t):
        total = timedelta()
        for num, unit in re.findall(DURATION_TOKEN, t):
            n = int(num)
            if unit.startswith("d"):
                total += timedelta(days=n)
            elif unit.startswith("h"):
                total += timedelta(hours=n)
            elif unit.startswith("m"):
                total += timedelta(minutes=n)
            else:
                total += timedelta(seconds=n)
        return now + total

    # Wake-time form: [tomorrow] [at] <clock time>
    tomorrow = False
    m = re.match(r"^tomorrow\b\s*", t)
    if m:
        tomorrow = True
        t = t[m.end():]
    t = re.sub(r"^at\s+", "", t)

    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if tomorrow and not t:
        return midnight + timedelta(days=1)
    if t == "noon":
        hour, minute = 12, 0
    elif t == "midnight":
        return midnight + timedelta(days=1 if not tomorrow else 2)
    else:
        # Require a colon or am/pm so a bare ambiguous number isn't a time
        m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*(am|pm)?|(\d{1,2})\s*(am|pm)", t)
        if not m:
            return None
        if m.group(1) is not None:
            hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
        else:
            hour, minute, ampm = int(m.group(4)), 0, m.group(5)
        if hour > 23 or minute > 59:
            return None
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0

    wake = midnight + timedelta(days=1 if tomorrow else 0, hours=hour, minutes=minute)
    if not tomorrow and wake <= now:
        wake += timedelta(days=1)
    return wake


def format_wake(dt, now=None):
    """Format a wake time for display, e.g. '4:15 PM CDT' or 'tomorrow 9:00 AM CDT'."""
    now = now or datetime.now()
    tz = now.astimezone().tzname() or ""
    clock = dt.strftime("%I:%M %p").lstrip("0")
    if dt.date() == now.date():
        day = ""
    elif dt.date() == now.date() + timedelta(days=1):
        day = "tomorrow "
    else:
        day = f"{dt.strftime('%a %b')} {dt.day}, "
    return f"{day}{clock} {tz}".rstrip()


def snoozed_until(item, now=None):
    """Return the local datetime a task is snoozed until, or None if not snoozed.

    Marvin stores a one-time snooze in itemSnoozeTime (epoch ms wake time) and a
    daily "permanent" snooze in permaSnoozeTime ("HH:mm"). While snoozed, the app
    hides the task everywhere except the master list.
    """
    now = now or datetime.now()
    ts = item.get("itemSnoozeTime")
    if ts and ts / 1000 > now.timestamp():
        return datetime.fromtimestamp(ts / 1000)
    perma = item.get("permaSnoozeTime")
    if perma:
        try:
            hour, minute = map(int, perma.split(":"))
            wake = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if wake > now:
                return wake
        except (ValueError, AttributeError):
            pass
    return None


def cmd_snooze(args):
    """Snooze a task: hide it from today's view until a wake time."""
    text = " ".join(args.duration)
    now = datetime.now()
    wake = parse_snooze_until(text, now)
    if not wake:
        print(f"Error: Could not parse duration/time '{text}'")
        print("Examples: 4h, 30m, 2h30m, 2d, '4 hours', 9am, 16:30, 'tomorrow 9am'")
        sys.exit(1)
    if wake <= now:
        print(f"Error: Wake time {format_wake(wake, now)} is not in the future")
        sys.exit(1)
    if wake - now > timedelta(days=366):
        print(f"Error: Wake time {format_wake(wake, now)} is more than a year out — probably a typo")
        sys.exit(1)

    item = resolve_task(args.task_id)
    title = item.get("title", "Untitled")

    now_ms = int(now.timestamp() * 1000)
    data = {
        "itemId": item["_id"],
        "setters": [
            {"key": "itemSnoozeTime", "val": int(wake.timestamp() * 1000)},
            {"key": "fieldUpdates.itemSnoozeTime", "val": now_ms},
            {"key": "updatedAt", "val": now_ms},
        ],
    }
    api_request("POST", "doc/update", data, full_access=True)
    print(f"✓ Snoozed '{title[:50]}' until {format_wake(wake, now)}")


def cmd_unsnooze(args):
    """Clear a task's snooze so it shows in today's view again."""
    item = resolve_task(args.task_id)
    title = item.get("title", "Untitled")

    if not item.get("itemSnoozeTime"):
        print(f"'{title[:50]}' isn't snoozed.")
        return

    now_ms = int(datetime.now().timestamp() * 1000)
    data = {
        "itemId": item["_id"],
        "setters": [
            {"key": "itemSnoozeTime", "val": None},
            {"key": "fieldUpdates.itemSnoozeTime", "val": now_ms},
            {"key": "updatedAt", "val": now_ms},
        ],
    }
    api_request("POST", "doc/update", data, full_access=True)
    print(f"✓ Unsnoozed '{title[:50]}'")


def cmd_snoozed(args):
    """List currently snoozed tasks with their wake times."""
    today_items = api_request("GET", "todayItems", full_access=True) or []
    due_items = api_request("GET", "dueItems", full_access=True) or []
    items = gather_items(today_items, due_items)

    now = datetime.now()
    snoozed = [(i, snoozed_until(i, now)) for i in items if not i.get("done")]
    snoozed = [(i, w) for i, w in snoozed if w]
    if not snoozed:
        print("No snoozed tasks.")
        return

    snoozed.sort(key=lambda pair: pair[1])
    print("Snoozed tasks:")
    print("-" * 40)
    uids = display_ids([i for i, _ in snoozed])
    for item, wake in snoozed:
        _, title, time_str, sid = format_task(item, uids)
        print(f"  💤 {title}{time_str}  until {format_wake(wake, now)}  ({sid})")


def cmd_search(args):
    """Search tasks by title (includes subtasks)."""
    query = " ".join(args.query).lower()
    if not query:
        print("Error: Search query required")
        sys.exit(1)

    today_items = api_request("GET", "todayItems", full_access=True) or []
    due_items = api_request("GET", "dueItems", full_access=True) or []
    sources = [today_items, due_items, upcoming_task_pool()]

    today = datetime.now().date()
    if args.all:
        date_str = today.strftime("%Y-%m-%d")
        done_items = api_request("GET", "doneItems", {"date": date_str}, full_access=True) or []
        sources.append(done_items)

    all_items = gather_items(*sources)

    # Search tasks and subtasks
    matches = []
    subtask_matches = []  # (parent_title, subtask_dict)

    for item in all_items:
        if query in item.get("title", "").lower():
            matches.append(item)
        # Search embedded subtasks
        for sub in item.get("subtasks", {}).values():
            if query in sub.get("title", "").lower():
                subtask_matches.append((item.get("title", "Untitled"), sub))

    if not matches and not subtask_matches:
        print(f"No tasks found matching '{query}'")
        return

    print(f"Tasks matching '{query}':")
    print("-" * 40)

    uids = display_ids(matches, [sub for _, sub in subtask_matches])
    for item in matches:
        done, title, time_str, sid = format_task(item, uids)
        scheduled = item.get("day", "")
        due = item.get("dueDate", "")
        date_info = f" (scheduled: {scheduled})" if scheduled else ""
        date_info += f" (due: {due})" if due else ""
        print(f"  {done} {title}{time_str}{date_info}  ({sid})")

    for parent_title, sub in subtask_matches:
        sub_done = "✓" if sub.get("done") else "○"
        sub_title = sub.get("title", "Untitled")
        sub_sid = uids.get(sub.get("_id", "")) or short_id(sub.get("_id", ""))
        print(f"      {sub_done} {sub_title}  ({sub_sid})  [in: {parent_title}]")


def cmd_list(args):
    """List tasks with various filters."""
    # Determine which type of list to show
    if args.today:
        # Show today's tasks
        args.date = None
        args.all = False
        cmd_today(args)
    elif args.completed:
        # Show completed tasks
        args.days = 7
        cmd_completed(args)
    elif args.due:
        # Show due tasks
        cmd_due(args)
    elif args.projects:
        # Show projects/categories
        cmd_projects(args)
    elif args.labels:
        # Show labels
        cmd_labels(args)
    else:
        # Default to today if no flag specified
        args.date = None
        args.all = False
        cmd_today(args)


def cmd_reorder(args):
    """Reorder today's tasks."""
    task_refs = args.task_ids
    items = api_request("GET", "todayItems", full_access=True)

    if not items:
        print("No tasks found for today.")
        sys.exit(1)

    # Build lookup: exact full id -> item. Every shorter ref goes through the
    # prefix match below, which refuses a ref that names more than one task.
    id_to_item = {item["_id"]: item for item in items if item.get("_id")}

    # Resolve task refs to items
    ordered_items = []
    for ref in task_refs:
        # Try exact match first
        if ref in id_to_item:
            ordered_items.append(id_to_item[ref])
        else:
            # Try prefix match against full ids, deduped by id
            matches = {
                item["_id"]: item
                for item in items
                if item.get("_id", "").startswith(ref)
            }
            if len(matches) == 1:
                ordered_items.append(next(iter(matches.values())))
            elif len(matches) > 1:
                print(f"Ambiguous task ref '{ref}' - matches multiple tasks:")
                for mid in matches:
                    print(f"      {mid}")
                sys.exit(1)
            else:
                print(f"No task found matching '{ref}'")
                sys.exit(1)

    # Update ranks
    now = int(datetime.now().timestamp() * 1000)
    for new_rank, item in enumerate(ordered_items, 1):
        task_id = item["_id"]
        data = {
            "itemId": task_id,
            "setters": [
                {"key": "rank", "val": new_rank},
                {"key": "fieldUpdates.rank", "val": now},
                {"key": "updatedAt", "val": now},
            ],
        }
        api_request("POST", "doc/update", data, full_access=True)

    print(f"✓ Reordered {len(ordered_items)} tasks")

    # Show new order
    print("\nNew order:")
    for idx, item in enumerate(ordered_items, 1):
        title = item.get("title", "Untitled")
        print(f"  {idx}. {title[:50]}")


RECURRING_INSTANCE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_[0-9a-f-]{36}$")


def cmd_skip_overdue(args):
    """Dismiss overdue instances of recurring tasks (skip the occurrence).

    For each overdue occurrence found it (1) adds the date to the recurring
    master's `deletedDates` so it won't regenerate, and (2) deletes the
    materialized instance doc. Both steps are needed: deletedDates alone won't
    hide an already-materialized doc, and deleting the doc alone lets the server
    regenerate the occurrence on the next read. The recurrence itself is left
    intact and nothing is marked done.
    """
    title_filter = " ".join(args.title).strip().lower() if args.title else None
    today = datetime.now().date()
    start = today - timedelta(days=args.days)

    # 1. Scan past days for incomplete, materialized recurring instances.
    #    Materialized recurring instances have a compound "<date>_<uuid>" _id.
    scope = f" matching '{' '.join(args.title)}'" if title_filter else ""
    print(f"Scanning {start} .. {today - timedelta(days=1)} for overdue recurring instances{scope}...")
    found = []  # each: {date, title, id, master}
    d = start
    while d < today:
        date_str = d.isoformat()
        items = api_request("GET", "todayItems", {"date": date_str}, full_access=True) or []
        for it in items:
            if it.get("done"):
                continue
            full_id = it.get("_id", "")
            if not RECURRING_INSTANCE_RE.match(full_id):
                continue
            title = it.get("title", "Untitled")
            if title_filter and title_filter not in title.lower():
                continue
            found.append({
                "date": date_str,
                "title": title,
                "id": full_id,
                "master": full_id.split("_", 1)[-1],
            })
        d += timedelta(days=1)

    if not found:
        print(f"No overdue recurring instances{scope} in the last {args.days} days.")
        return

    # Group by title for a readable summary.
    by_title = {}
    for f in found:
        by_title.setdefault(f["title"], []).append(f)

    print(f"\nFound {len(found)} overdue recurring instance(s) across {len(by_title)} task(s):")
    for title, insts in sorted(by_title.items(), key=lambda x: -len(x[1])):
        dates = sorted(i["date"] for i in insts)
        label = title if len(title) <= 50 else title[:47] + "..."
        span = dates[0] if len(dates) == 1 else f"{dates[0]} .. {dates[-1]}"
        print(f"  {len(insts):3d}  {label:50s}  {span}")

    if args.dry_run:
        print("\n(dry run — nothing changed. Re-run without --dry-run to clear these.)")
        return

    # 2. Add the overdue dates to each recurring master's deletedDates.
    masters = {}
    for f in found:
        masters.setdefault(f["master"], set()).add(f["date"])

    print()
    for master_id, dates in masters.items():
        doc = api_request("GET", "doc", {"id": master_id}, full_access=True, tolerant=True)
        if not isinstance(doc, dict) or not doc.get("_id"):
            print(f"  ! Could not read master {master_id[:8]} — its instances may reappear.")
            continue
        current = doc.get("deletedDates") or []
        merged = sorted(set(current) | dates)
        now = int(datetime.now().timestamp() * 1000)
        api_request("POST", "doc/update", {
            "itemId": master_id,
            "setters": [
                {"key": "deletedDates", "val": merged},
                {"key": "fieldUpdates.deletedDates", "val": now},
                {"key": "updatedAt", "val": now},
            ],
        }, full_access=True, tolerant=True)

    # 3. Delete the materialized instance docs.
    cleared = 0
    for f in found:
        api_request("POST", "doc/delete", {"itemId": f["id"]}, full_access=True, tolerant=True)
        cleared += 1

    print(f"✓ Cleared {cleared} overdue recurring instance(s) across {len(masters)} task(s).")
    print("  Recurrences left intact; nothing marked done.")


def get_tz_offset():
    """Get timezone offset in minutes."""
    now = datetime.now(timezone.utc).astimezone()
    return int(-now.utcoffset().total_seconds() / 60)


def main():
    parser = argparse.ArgumentParser(
        description="CLI for Amazing Marvin",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  marvin add "Buy groceries +today"
  marvin add "Review PR #Work @urgent"
  marvin add "Fix tests" -p abc123   # Add subtask to parent abc123
  marvin add "Deploy fix" -d next monday  # Schedule for a date
  marvin list                        # List today's tasks (default)
  marvin list --today                # List today's tasks
  marvin list --completed            # List completed tasks
  marvin list --due                  # List tasks due soon
  marvin today
  marvin day last friday
  marvin day yesterday --incomplete
  marvin day 2024-01-15
  marvin subtasks abc123             # List subtasks (incl. unscheduled children)
  marvin note abc123                 # Print a task's note
  marvin note abc123 "Ran into X"    # Set the note
  marvin note abc123 "Also Y" -A     # Append a line to the note
  marvin rm abc123                   # Delete a task (confirmation required)
  marvin today -S                    # Show unscheduled child tasks inline
  marvin search meeting              # Searches tasks and subtasks
  marvin done abc123                 # Works for tasks and subtasks
  marvin completed               # Show completed tasks from last 7 days
  marvin completed --days 14     # Show completed tasks from last 14 days
  marvin reorder abc123 def456 ghi789
  marvin snooze abc123 4h            # Hide from today until 4 hours from now
  marvin snooze abc123 tomorrow 9am  # Hide until tomorrow morning
  marvin unsnooze abc123             # Wake a snoozed task early
  marvin snoozed                     # List snoozed tasks with wake times
  marvin skip-overdue                # Clear ALL overdue recurring instances
  marvin skip-overdue "Review PRs"   # Clear only that task's overdue instances
  marvin skip-overdue --dry-run      # Preview what would be cleared
  marvin projects
  marvin labels

Date formats for 'day' command:
  YYYY-MM-DD          Specific date (e.g., 2024-01-15)
  today, yesterday    Relative to today
  tomorrow            Relative to today
  last friday         Most recent Friday
  this monday         This week's Monday
  next tuesday        Next occurrence of Tuesday
  3 days ago          N days in the past
  friday              Same as "last friday"
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # setup
    sub = subparsers.add_parser("setup", help="Configure API tokens")
    sub.set_defaults(func=cmd_setup)

    # list - convenience command that delegates to other commands
    sub = subparsers.add_parser("list", help="List tasks (default: today's tasks)")
    sub.add_argument("--today", "-t", action="store_true", help="List today's tasks (default)")
    sub.add_argument("--completed", "-c", action="store_true", help="List completed tasks from last 7 days")
    sub.add_argument("--due", "-d", action="store_true", help="List tasks due soon")
    sub.add_argument("--projects", "-p", action="store_true", help="List projects/categories")
    sub.add_argument("--labels", "-l", action="store_true", help="List labels")
    sub.add_argument("--all", "-a", action="store_true", help="Include completed tasks (for --today)")
    sub.add_argument("--snoozed", "-s", action="store_true", help="Show snoozed tasks (hidden by default, for --today)")
    sub.add_argument("--subtasks", "-S", action="store_true", help="Fetch child tasks too (for --today)")
    sub.set_defaults(func=cmd_list)

    # add
    sub = subparsers.add_parser("add", help="Add a new task or subtask")
    sub.add_argument("title", nargs="+", help="Task title (supports +today #Category @label)")
    sub.add_argument("--parent", "-p", help="Parent task ID to create as subtask")
    sub.add_argument("--day", "-d", nargs="+", help="Schedule for date (e.g., 2026-03-30, tomorrow, 'next monday')")
    sub.set_defaults(func=cmd_add)

    # today
    sub = subparsers.add_parser("today", help="List today's tasks")
    sub.add_argument("--date", "-d", help="Date (YYYY-MM-DD or natural language like 'yesterday')")
    sub.add_argument("--all", "-a", action="store_true", help="Include completed tasks")
    sub.add_argument("--snoozed", "-s", action="store_true", help="Show snoozed tasks (hidden by default)")
    sub.add_argument("--subtasks", "-S", action="store_true",
                     help="Fetch each task's child tasks, including unscheduled ones (1 request per task)")
    sub.set_defaults(func=cmd_today)

    # day - new command for viewing tasks by date
    sub = subparsers.add_parser("day", help="List tasks for a specific day")
    sub.add_argument("date", nargs="*", help="Date (e.g., 'last friday', 'yesterday', '2024-01-15')")
    sub.add_argument("--incomplete", "-i", action="store_true", help="Show only incomplete tasks")
    sub.add_argument("--all", "-a", action="store_true", help="Include completed tasks")
    sub.add_argument("--snoozed", "-s", action="store_true", help="Show snoozed tasks (hidden by default)")
    sub.add_argument("--subtasks", "-S", action="store_true",
                     help="Fetch each task's child tasks, including unscheduled ones (1 request per task)")
    sub.set_defaults(func=cmd_day)

    # search - new command for searching tasks
    sub = subparsers.add_parser("search", help="Search tasks by title")
    sub.add_argument("query", nargs="+", help="Search query")
    sub.add_argument("--all", "-a", action="store_true", help="Include completed tasks")
    sub.set_defaults(func=cmd_search)

    # move - reschedule a task to a different day
    sub = subparsers.add_parser("move", help="Move/reschedule a task to a different day")
    sub.add_argument("task_id", help="Task ID (or prefix)")
    sub.add_argument("date", nargs="*", help="Target date (default: today)")
    sub.add_argument("--from", "-f", dest="from_date",
                     help="Source date to search for task (one value; quote a multi-word date)")
    sub.set_defaults(func=cmd_move)

    # snooze - hide a task from today's view until a wake time
    sub = subparsers.add_parser("snooze", help="Hide a task from today's view until a wake time")
    sub.add_argument("task_id", help="Task ID (or prefix)")
    sub.add_argument("duration", nargs="+",
                     help="Duration (4h, 30m, 2h30m, '4 hours') or wake time (9am, 16:30, 'tomorrow 9am')")
    sub.set_defaults(func=cmd_snooze)

    # unsnooze - clear a snooze
    sub = subparsers.add_parser("unsnooze", help="Clear a task's snooze so it shows again")
    sub.add_argument("task_id", help="Task ID (or prefix)")
    sub.set_defaults(func=cmd_unsnooze)

    # snoozed - list snoozed tasks
    sub = subparsers.add_parser("snoozed", help="List currently snoozed tasks with wake times")
    sub.set_defaults(func=cmd_snoozed)

    # estimate - set time estimate for a task
    sub = subparsers.add_parser("estimate", help="Set time estimate for a task")
    sub.add_argument("task_id", help="Task ID (or prefix)")
    sub.add_argument("minutes", type=int, help="Time estimate in minutes")
    sub.set_defaults(func=cmd_estimate)

    # done
    sub = subparsers.add_parser("done", help="Mark a task or subtask as done")
    sub.add_argument("task_id", help="Task or subtask ID (or prefix)")
    sub.set_defaults(func=cmd_done)

    # subtasks
    sub = subparsers.add_parser("subtasks", help="List subtasks of a task")
    sub.add_argument("task_id", help="Parent task ID (or prefix)")
    sub.add_argument("--all", "-a", action="store_true", help="Include completed tasks in search")
    sub.set_defaults(func=cmd_subtasks)

    # note
    sub = subparsers.add_parser("note", help="Read or write a task's note")
    sub.add_argument("task_id", help="Task ID (or prefix)")
    sub.add_argument("text", nargs="*", help="Note text (omit to print the current note)")
    sub.add_argument("--append", "-A", action="store_true",
                     help="Append to the existing note instead of replacing it")
    sub.set_defaults(func=cmd_note)

    # rm
    sub = subparsers.add_parser("rm", help="Delete a task permanently (asks for confirmation)")
    sub.add_argument("task_id", help="Task ID (or prefix)")
    sub.add_argument("--force", "-f", action="store_true", help="Skip the confirmation prompt")
    sub.set_defaults(func=cmd_rm)

    # completed
    sub = subparsers.add_parser("completed", help="List completed tasks from last N days")
    sub.add_argument("--days", "-d", type=int, default=7, help="Number of days to check (default: 7)")
    sub.set_defaults(func=cmd_completed)

    # projects
    sub = subparsers.add_parser("projects", help="List categories/projects")
    sub.set_defaults(func=cmd_projects)

    # labels
    sub = subparsers.add_parser("labels", help="List labels")
    sub.set_defaults(func=cmd_labels)

    # due
    sub = subparsers.add_parser("due", help="List tasks due soon")
    sub.set_defaults(func=cmd_due)

    # category
    sub = subparsers.add_parser("category", help="List tasks in a category/project")
    sub.add_argument("name", nargs="+", help="Category/project name or 8-char id")
    sub.add_argument("--all", "-a", action="store_true", help="Include completed tasks")
    sub.add_argument("--subtasks", "-S", action="store_true",
                     help="Also list each task's child tasks (1 request per task)")
    sub.set_defaults(func=cmd_category)

    # backlog
    sub = subparsers.add_parser(
        "backlog", help="List open, unscheduled tasks (nothing gets forgotten)"
    )
    sub.add_argument(
        "--uncategorized", "-u", action="store_true",
        help="Only the uncategorized root bucket (skip walking categories)",
    )
    sub.add_argument(
        "--subtasks", "-S", action="store_true",
        help="Also list child tasks of each backlog task (1 request per task)",
    )
    sub.set_defaults(func=cmd_backlog)

    # reorder
    sub = subparsers.add_parser("reorder", help="Reorder today's tasks")
    sub.add_argument("task_ids", nargs="+", help="Task IDs in desired order")
    sub.set_defaults(func=cmd_reorder)

    # skip-overdue
    sub = subparsers.add_parser(
        "skip-overdue",
        help="Dismiss overdue instances of recurring tasks (keeps them recurring)",
    )
    sub.add_argument("title", nargs="*", help="Only clear instances whose title contains this text")
    sub.add_argument("--days", "-d", type=int, default=90, help="How many days back to scan (default: 90)")
    sub.add_argument("--dry-run", "-n", action="store_true", help="Show what would be cleared without changing anything")
    sub.set_defaults(func=cmd_skip_overdue)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()

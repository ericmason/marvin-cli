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
    marvin subtasks <task_id>       # List subtasks of a task
    marvin search <query>           # Search tasks and subtasks by title
    marvin done <task_id>           # Mark task or subtask complete
    marvin completed                # List completed tasks from last 7 days
    marvin completed --days 14      # List completed tasks from last 14 days
    marvin reorder <id1> <id2> ...  # Reorder tasks
    marvin skip-overdue             # Dismiss overdue recurring task instances
    marvin projects                 # List projects/categories
    marvin labels                   # List labels
    marvin setup                    # Configure API token
"""

import argparse
import json
import os
import re
import sys
import time
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

    Retries with backoff on HTTP 429 (the API's "Too Many Requests" rate limit).
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
            if status == 429 and attempt < max_retries:
                time.sleep(3 * (attempt + 1))
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


def find_task_by_ref(task_ref, items):
    """Find a task by ID reference (full ID prefix or short ID prefix).

    Returns the matched item or exits with an error.
    """
    def id_matches(item):
        full_id = item.get("_id", "")
        uuid_part = full_id.split("_", 1)[-1] if "_" in full_id else full_id
        return full_id.startswith(task_ref) or uuid_part.startswith(task_ref)

    matches = [i for i in items if id_matches(i)]

    if not matches:
        print(f"No task found matching '{task_ref}'")
        sys.exit(1)
    if len(matches) > 1:
        print(f"Multiple tasks match '{task_ref}':")
        for idx, m in enumerate(matches, 1):
            print(f"  {idx}. {m.get('title', 'Untitled')}")
        sys.exit(1)
    return matches[0]


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

    Handles recurring task IDs (e.g., '2026-03-31_abc12345') by splitting on
    the first underscore and using the part after it.
    """
    if not raw_id:
        return ""
    return raw_id.split("_", 1)[-1][:8] if "_" in raw_id else raw_id[:8]


def format_task(item):
    """Format a task for display. Returns (done_marker, title, time_str, short_id)."""
    done = "✓" if item.get("done") else "○"
    title = item.get("title", "Untitled")
    sid = short_id(item.get("_id", ""))
    time_est = item.get("timeEstimate")
    time_str = f" [{time_est // 60000}m]" if time_est else ""
    return done, title, time_str, sid


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
        today_items = api_request("GET", "todayItems", full_access=True) or []
        due_items = api_request("GET", "dueItems", full_access=True) or []
        all_items = gather_items(today_items, due_items)
        parent = find_task_by_ref(args.parent, all_items)
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
        print(f"  ID: {task_id[:8]}...")
    else:
        print(f"✓ Added: {title}")
        if parent_title:
            print(f"  Parent: {parent_title}")
        if args.day:
            print(f"  Scheduled: {day_str}")


def cmd_today(args):
    """List today's tasks."""
    date_str = parse_date(args.date) if args.date else datetime.now().strftime("%Y-%m-%d")
    show_tasks_for_date(date_str, include_completed=args.all)


def cmd_day(args):
    """List tasks for a specific day."""
    date_input = " ".join(args.date) if args.date else "today"
    date_str = parse_date(date_input)
    show_tasks_for_date(date_str, show_incomplete_only=args.incomplete, include_completed=args.all)


def show_tasks_for_date(date_str, show_incomplete_only=False, include_completed=False):
    """Display tasks for a given date."""
    today_items = api_request("GET", "todayItems", {"date": date_str}, full_access=True) or []

    if include_completed:
        done_items = api_request("GET", "doneItems", {"date": date_str}, full_access=True) or []
        items = gather_items(today_items, done_items)
    else:
        items = today_items

    if not items:
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
    for parent_id in children_by_parent:
        children_by_parent[parent_id] = sorted(
            children_by_parent[parent_id], key=lambda x: x.get("rank", 999999)
        )

    print(f"Tasks for {date_str}:")
    print("-" * 40)

    for idx, item in enumerate(top_level, 1):
        done, title, time_str, sid = format_task(item)
        print(f"  {idx}. {done} {title}{time_str}  ({sid})")

        # Print child items (separate tasks with parentId)
        item_id = item.get("_id")
        if item_id in children_by_parent:
            for subtask in children_by_parent[item_id]:
                done, title, time_str, sid = format_task(subtask)
                print(f"      {done} {title}{time_str}  ({sid})")

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
                sub_sid = short_id(subtask.get("_id", ""))
                print(f"      {sub_done} {sub_title}  ({sub_sid})")


def find_embedded_subtask(task_ref, items):
    """Search for an embedded subtask matching task_ref within items.

    Returns (parent_item, subtask_key, subtask_dict) or (None, None, None).
    """
    for item in items:
        embedded = item.get("subtasks", {})
        if not embedded:
            continue
        for key, sub in embedded.items():
            sub_id = sub.get("_id", "")
            if sub_id.startswith(task_ref) or key.startswith(task_ref):
                return item, key, sub
    return None, None, None


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

    if not items:
        print("No tasks found for today.")
        sys.exit(1)

    # Check if it's a number (list index for top-level tasks)
    if task_ref.isdigit():
        idx = int(task_ref)
        if idx < 1 or idx > len(items):
            print(f"Invalid task number. Use 1-{len(items)}.")
            sys.exit(1)
        item = items[idx - 1]
        task_id = item["_id"]
        title = item.get("title", "Untitled")
        data = {"itemId": task_id, "timeZoneOffset": get_tz_offset()}
        api_request("POST", "markDone", data)
        print(f"✓ Marked done: {title}")
        return

    # Try matching as a regular task (including child tasks)
    def id_matches(item):
        full_id = item.get("_id", "")
        uuid_part = full_id.split("_", 1)[-1] if "_" in full_id else full_id
        return full_id.startswith(task_ref) or uuid_part.startswith(task_ref)

    matches = [i for i in items if id_matches(i)]

    if len(matches) == 1:
        item = matches[0]
        data = {"itemId": item["_id"], "timeZoneOffset": get_tz_offset()}
        api_request("POST", "markDone", data)
        print(f"✓ Marked done: {item.get('title', 'Untitled')}")
        return

    if len(matches) > 1:
        # If all matches are recurring instances of the same task (same title,
        # same base ID), complete the earliest one by due/scheduled date
        titles = set(m.get("title", "") for m in matches)
        base_ids = set(
            (m.get("_id", "").split("_", 1)[-1][:8] if "_" in m.get("_id", "") else m.get("_id", "")[:8])
            for m in matches
        )
        if len(titles) == 1 and len(base_ids) == 1:
            # All recurring instances — pick the earliest by dueDate or day
            def sort_key(m):
                return m.get("dueDate") or m.get("day") or "9999-99-99"
            matches.sort(key=sort_key)
            item = matches[0]
            date_label = item.get("dueDate") or item.get("day") or ""
            data = {"itemId": item["_id"], "timeZoneOffset": get_tz_offset()}
            api_request("POST", "markDone", data)
            date_suffix = f" (due: {date_label})" if date_label else ""
            print(f"✓ Marked done: {item.get('title', 'Untitled')}{date_suffix}")
            return

        print(f"Multiple tasks match '{task_ref}':")
        for idx, m in enumerate(matches, 1):
            date_label = m.get("dueDate") or m.get("day") or ""
            date_suffix = f" (due: {date_label})" if date_label else ""
            print(f"  {idx}. {m.get('title', 'Untitled')}{date_suffix}")
        sys.exit(1)

    # No regular task matched — check embedded subtasks
    parent, sub_key, subtask = find_embedded_subtask(task_ref, items)
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
            _, title, time_str, sid = format_task(item)
            print(f"  ✓ {title}{time_str}  ({sid})")
            # Show completed embedded subtasks
            for sub in sorted(
                item.get("subtasks", {}).values(),
                key=lambda x: x.get("rank", 999999),
            ):
                if sub.get("done"):
                    sub_title = sub.get("title", "Untitled")
                    sub_sid = short_id(sub.get("_id", ""))
                    print(f"      ✓ {sub_title}  ({sub_sid})")
                    total += 1

    print(f"\n{total} tasks completed in {days} days")


def cmd_subtasks(args):
    """List subtasks of a specific task."""
    task_ref = args.task_id

    today_items = api_request("GET", "todayItems", full_access=True) or []
    due_items = api_request("GET", "dueItems", full_access=True) or []
    sources = [today_items, due_items]

    if args.all:
        date_str = datetime.now().strftime("%Y-%m-%d")
        done_items = api_request("GET", "doneItems", {"date": date_str}, full_access=True) or []
        sources.append(done_items)

    all_items = gather_items(*sources)

    parent = find_task_by_ref(task_ref, all_items)
    parent_title = parent.get("title", "Untitled")

    # Collect child tasks (separate tasks with parentId pointing here)
    parent_id = parent.get("_id")
    children = [i for i in all_items if i.get("parentId") == parent_id]
    children = sorted(children, key=lambda x: x.get("rank", 999999))

    # Collect embedded subtasks
    embedded = parent.get("subtasks", {})
    sorted_embedded = sorted(
        embedded.values(), key=lambda x: x.get("rank", 999999)
    )

    if not children and not sorted_embedded:
        print(f"No subtasks for: {parent_title}")
        return

    print(f"Subtasks of: {parent_title}")
    print("-" * 40)

    for child in children:
        done, title, time_str, sid = format_task(child)
        print(f"  {done} {title}{time_str}  ({sid})")

    for sub in sorted_embedded:
        sub_done = "✓" if sub.get("done") else "○"
        sub_title = sub.get("title", "Untitled")
        sub_sid = short_id(sub.get("_id", ""))
        print(f"  {sub_done} {sub_title}  ({sub_sid})")

    total = len(children) + len(sorted_embedded)
    print(f"\n{total} subtask(s)")


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


def cmd_due(args):
    """List tasks due soon."""
    items = api_request("GET", "dueItems", full_access=True)

    if not items:
        print("No due tasks.")
        return

    print("Due tasks:")
    print("-" * 40)

    for item in items:
        _, title, time_str, sid = format_task(item)
        due = item.get("dueDate", "no date")
        print(f"  ! {title}{time_str}  (due: {due}) ({sid})")


def cmd_estimate(args):
    """Set time estimate for a task."""
    task_ref = args.task_id
    minutes = args.minutes

    today_items = api_request("GET", "todayItems", full_access=True) or []
    due_items = api_request("GET", "dueItems", full_access=True) or []
    all_items = gather_items(today_items, due_items)

    item = find_task_by_ref(task_ref, all_items)
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

    today_items = api_request("GET", "todayItems", full_access=True) or []
    due_items = api_request("GET", "dueItems", full_access=True) or []
    sources = [today_items, due_items]

    if args.from_date:
        source_date = parse_date(" ".join(args.from_date))
        source_items = api_request("GET", "todayItems", {"date": source_date}, full_access=True) or []
        sources.append(source_items)

    # Also search upcoming 14 days to find scheduled tasks
    today = datetime.now().date()
    for i in range(1, 15):
        future_date = (today + timedelta(days=i)).strftime("%Y-%m-%d")
        future_items = api_request("GET", "todayItems", {"date": future_date}, full_access=True) or []
        if future_items:
            sources.append(future_items)

    all_items = gather_items(*sources)
    item = find_task_by_ref(task_ref, all_items)
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


def cmd_search(args):
    """Search tasks by title (includes subtasks)."""
    query = " ".join(args.query).lower()
    if not query:
        print("Error: Search query required")
        sys.exit(1)

    today_items = api_request("GET", "todayItems", full_access=True) or []
    due_items = api_request("GET", "dueItems", full_access=True) or []
    sources = [today_items, due_items]

    # Search upcoming days (next 14 days) to find scheduled tasks
    today = datetime.now().date()
    for i in range(1, 15):
        future_date = (today + timedelta(days=i)).strftime("%Y-%m-%d")
        future_items = api_request("GET", "todayItems", {"date": future_date}, full_access=True) or []
        if future_items:
            sources.append(future_items)

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

    for item in matches:
        done, title, time_str, sid = format_task(item)
        scheduled = item.get("day", "")
        due = item.get("dueDate", "")
        date_info = f" (scheduled: {scheduled})" if scheduled else ""
        date_info += f" (due: {due})" if due else ""
        print(f"  {done} {title}{time_str}{date_info}  ({sid})")

    for parent_title, sub in subtask_matches:
        sub_done = "✓" if sub.get("done") else "○"
        sub_title = sub.get("title", "Untitled")
        sub_sid = short_id(sub.get("_id", ""))
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

    # Build lookup: short_id -> full item
    id_to_item = {}
    for item in items:
        sid = short_id(item.get("_id", ""))
        id_to_item[sid] = item
        # Also map by full ID prefix
        full_id = item.get("_id", "")
        if full_id:
            id_to_item[full_id[:8]] = item

    # Resolve task refs to items
    ordered_items = []
    for ref in task_refs:
        # Try exact match first
        if ref in id_to_item:
            ordered_items.append(id_to_item[ref])
        else:
            # Try prefix match
            matches = [item for sid, item in id_to_item.items() if sid.startswith(ref)]
            if len(matches) == 1:
                ordered_items.append(matches[0])
            elif len(matches) > 1:
                print(f"Ambiguous task ref '{ref}' - matches multiple tasks")
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
  marvin subtasks abc123             # List subtasks of a task
  marvin search meeting              # Searches tasks and subtasks
  marvin done abc123                 # Works for tasks and subtasks
  marvin completed               # Show completed tasks from last 7 days
  marvin completed --days 14     # Show completed tasks from last 14 days
  marvin reorder abc123 def456 ghi789
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
    sub.set_defaults(func=cmd_today)

    # day - new command for viewing tasks by date
    sub = subparsers.add_parser("day", help="List tasks for a specific day")
    sub.add_argument("date", nargs="*", help="Date (e.g., 'last friday', 'yesterday', '2024-01-15')")
    sub.add_argument("--incomplete", "-i", action="store_true", help="Show only incomplete tasks")
    sub.add_argument("--all", "-a", action="store_true", help="Include completed tasks")
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
    sub.add_argument("--from", "-f", dest="from_date", nargs="+", help="Source date to search for task")
    sub.set_defaults(func=cmd_move)

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

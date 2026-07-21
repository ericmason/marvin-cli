# marvin-cli

A simple, fast command-line interface for [Amazing Marvin](https://amazingmarvin.com/) — view, add, schedule, complete, and reorder your tasks without leaving the terminal.

It talks directly to the official Amazing Marvin API, so there's nothing to sync and no local database to keep in step.

## Features

- View today's tasks, any specific day, due items, and completed history
- Add tasks and subtasks with inline `+today`, `#Category`, and `@label` modifiers
- Complete tasks and subtasks, set time estimates, and reorder by priority
- Move/reschedule tasks to any day (natural-language dates supported)
- **Snooze** tasks for hours or minutes — hide from today's view until a wake time (`4h`, `30m`, `tomorrow 9am`)
- Search across tasks and subtasks
- **`skip-overdue`** — dismiss the pile-up of overdue *recurring* task instances without marking them done and without stopping the recurrence
- Automatic backoff/retry on API rate limits and transient 5xx errors

## Requirements

- Python 3.8+
- [`requests`](https://pypi.org/project/requests/)
- An Amazing Marvin account with API access enabled

## Install

### Option A — symlink installer (recommended)

```bash
git clone https://github.com/ericmason/marvin-cli.git
cd marvin-cli
./install.sh
```

This symlinks `marvin` into `/usr/local/bin` and installs `requests` if needed.

### Option B — pip

```bash
pip install git+https://github.com/ericmason/marvin-cli.git
```

## Setup

Grab your API tokens from **https://app.amazingmarvin.com/pre?api**, then run:

```bash
marvin setup
```

- **API Token** — required for creating/updating tasks
- **Full Access Token** — required for reading your data (today's list, due items, etc.)

Tokens are stored locally at `~/.config/marvin/config.json`. This file is never
committed — keep it private.

## Usage

```bash
marvin today                        # Today's tasks (with subtasks), by priority
marvin day yesterday -i             # A specific day; -i = incomplete only
marvin due                          # Tasks with due dates
marvin completed --days 14          # Completed tasks from the last 14 days

marvin add "Buy groceries +today"   # Add a task scheduled for today
marvin add "Review PR #Work @urgent"
marvin add "Fix tests" -p abc123    # Add a subtask under parent abc123
marvin add "Deploy" -d next monday  # Schedule for a date

marvin done abc123                  # Complete a task or subtask
marvin estimate abc123 30           # Set a 30-minute estimate
marvin move abc123 tomorrow         # Reschedule to another day
marvin snooze abc123 4h             # Hide from today until 4 hours from now
marvin unsnooze abc123              # Wake a snoozed task early
marvin snoozed                      # List snoozed tasks with wake times
marvin reorder id1 id2 id3          # Reorder today's tasks by priority
marvin search groceries             # Search tasks and subtasks

marvin subtasks abc123              # List subtasks of a task
marvin projects                     # List categories/projects
marvin labels                       # List labels
```

### Snoozing (sub-day rescheduling)

`move` reschedules by whole day; `snooze` is its sub-day counterpart — it hides
a task from today's view and brings it back after a duration or at a wake time:

```bash
marvin snooze abc123 4h             # Durations: 4h, 30m, 90min, 2h30m, 2d
marvin snooze abc123 "4 hours"      # Natural language works too
marvin snooze abc123 4:15pm         # Or a wake time: 9am, 16:30, noon
marvin snooze abc123 tomorrow 9am   # A past clock time rolls to tomorrow
marvin unsnooze abc123              # Wake it early
marvin snoozed                      # What's snoozed, and until when
```

This is the same snooze as the app's sleepy-moon icon (it sets the task's
`itemSnoozeTime`), so snoozes made in the CLI and the app see each other.
Matching the app, snoozed tasks are hidden from `today`/`day` (a `💤 N snoozed`
line shows the count) but still appear in `backlog` and `category`. Use
`--snoozed` / `-s` on `today`/`day` to show them inline with their wake times.

### Clearing overdue recurring tasks

Recurring tasks materialize an instance for every scheduled day. Left
incomplete, they accumulate into an overdue backlog that clutters your lists.
`skip-overdue` clears them cleanly:

```bash
marvin skip-overdue                 # Dismiss ALL overdue recurring instances
marvin skip-overdue "Review PRs"    # Only instances whose title matches
marvin skip-overdue --dry-run       # Preview what would be cleared
marvin skip-overdue --days 120      # Scan further back (default: 90 days)
```

It **dismisses** each overdue occurrence — it does **not** mark it done (so your
completed history stays honest) and does **not** stop the recurrence (today's
occurrence and all future ones are untouched). Prefer this over `marvin done`
for clearing a recurring backlog.

### Dates

Commands that take a date accept `YYYY-MM-DD` or natural language:
`today`, `yesterday`, `tomorrow`, `last friday`, `this monday`,
`next tuesday`, `3 days ago`.

## How task IDs work

Listings show an 8-character ID in parentheses, e.g. `(abc12345)`. Any command
that takes a task ID accepts that prefix. Recurring task instances have a
compound `YYYY-MM-DD_<uuid>` ID; the CLI matches on either part.

## License

[MIT](LICENSE) © Eric Mason

## Disclaimer

This is an unofficial, community-built tool and is not affiliated with or
endorsed by Amazing Marvin.

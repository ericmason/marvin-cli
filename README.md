# marvin-cli

A simple, fast command-line interface for [Amazing Marvin](https://amazingmarvin.com/) — view, add, schedule, complete, and reorder your tasks without leaving the terminal.

It talks directly to the official Amazing Marvin API, so there's nothing to sync and no local database to keep in step.

## Features

- View today's tasks, any specific day, due items, and completed history
- Add tasks and subtasks with inline `+today`, `#Category`, and `@label` modifiers
- Complete tasks and subtasks, set time estimates, and reorder by priority
- Move/reschedule tasks to any day (natural-language dates supported)
- Search across tasks and subtasks
- **`skip-overdue`** — dismiss the pile-up of overdue *recurring* task instances without marking them done and without stopping the recurrence
- Automatic backoff/retry on API rate limits

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
marvin move abc123 tomorrow         # Reschedule
marvin reorder id1 id2 id3          # Reorder today's tasks by priority
marvin search groceries             # Search tasks and subtasks

marvin subtasks abc123              # List subtasks of a task
marvin projects                     # List categories/projects
marvin labels                       # List labels
```

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

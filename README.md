# marvin-cli

A simple, fast command-line interface for [Amazing Marvin](https://amazingmarvin.com/) — view, add, schedule, complete, and reorder your tasks without leaving the terminal.

It talks directly to the official Amazing Marvin API, so there's nothing to sync and no local database to keep in step.

## Features

- View today's tasks, any specific day, due items, and completed history
- Add tasks and subtasks with inline `+today`, `#Category`, and `@label` modifiers
- Complete tasks and subtasks, set time estimates, and reorder by priority
- Read and write a task's **note**, and delete tasks with `rm`
- Move/reschedule tasks to any day (natural-language dates supported)
- **Snooze** tasks for hours or minutes — hide from today's view until a wake time (`4h`, `30m`, `tomorrow 9am`)
- Search across tasks and subtasks
- Ambiguous short IDs are a hard error listing the candidates, never a silent guess
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
marvin note abc123                  # Print a task's note
marvin note abc123 "Blocked on X"   # Set the note (-A/--append to add a line)
marvin rm abc123                    # Delete a task (asks for confirmation)
marvin projects                     # List categories/projects
marvin labels                       # List labels
```

### Subtasks

Marvin has two different things called subtasks and `marvin subtasks` shows both:

- **Child tasks** — full task documents whose `parentId` points at the parent
- **Embedded subtasks** — lightweight checklist entries stored inside the parent

```bash
marvin subtasks abc123              # Open subtasks (child tasks + embedded)
marvin subtasks abc123 -a           # Include completed ones
```

Child tasks are read from the API's `children` endpoint, which is the only place
an **unscheduled** child (`day: "unassigned"`) appears — such a task is returned
by none of the day-based endpoints, so it shows up in no other listing.

Because that costs one request per task, the day and category listings don't
fetch children by default. Add `-S` / `--subtasks` when you want them inline:

```bash
marvin today -S                     # Today's tasks with their child tasks
marvin day tomorrow -S
marvin category Priority -S
marvin backlog -S                   # Children of each unscheduled backlog task
```

Two limits worth knowing: the `children` endpoint omits **completed** children
(`subtasks -a` backfills them by scanning the done lists around today, so
long-completed ones aren't listed), and `backlog -S` only reaches children of
tasks that are themselves unscheduled — for an unscheduled child of a
*scheduled* parent, use `marvin subtasks <parent>`.

### Notes

```bash
marvin note abc123                       # Print the note
marvin note abc123 "Ran into rate limits"  # Replace the note
marvin note abc123 "Retried, worked" -A    # Append a line
```

### Deleting

```bash
marvin rm abc123                    # Prompts; type 'yes' to confirm
marvin rm abc123 --force            # Skip the prompt
```

`rm` deletes permanently and cannot be undone. It prints the task first and
warns when the task has child tasks — children are **not** deleted with the
parent and would be left orphaned. Without a TTY it refuses unless `--force` is
given. To clear overdue *recurring* instances use `skip-overdue` instead.

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

Listings show an ID in parentheses, e.g. `(abc12345)`, and any command that takes
a task ID accepts it. That ID is **the shortest prefix that is unique within the
list you are looking at — not a fixed 8 characters.**

This matters because Marvin IDs created in the same session share a long common
prefix and differ only near the end. Real example: a parent task and one of its
own children whose IDs agree for the first 30 characters:

```
fc365eada0d7d8efc3509e677c2f271f   the parent
fc365eada0d7d8efc3509e677c2f27d6   its child
```

So a "short ID" is frequently ambiguous. The CLI handles that as follows:

- **An ambiguous reference is an error, never a guess.** It prints every
  candidate with an ID long enough to tell them apart, and exits non-zero.
- **An exact ID always wins** over a prefix match.
- **Displayed IDs are lengthened as needed** so anything the CLI prints can be
  pasted straight back into another command.
- **Child tasks reached through `subtasks` / `-S` are shown with their full ID.**
  A task nested under another task is in no day list and is not a direct child of
  any category, so it can only be resolved by an exact ID lookup — a shortened
  prefix would not find it.

Recurring task instances have a compound `YYYY-MM-DD_<uuid>` ID and the CLI
matches on either part. Every instance of one recurring task shares the same
uuid, so no prefix can separate them; listings show the short form and
disambiguate with the scheduled/due date instead. `marvin done` on such a
reference completes the earliest outstanding instance.

## License

[MIT](LICENSE) © Eric Mason

## Disclaimer

This is an unofficial, community-built tool and is not affiliated with or
endorsed by Amazing Marvin.

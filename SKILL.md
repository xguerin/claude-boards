---
name: claude-boards
description: This skill should be used when the user asks to "post a message to the board", "check the board for messages", "leave a note for another agent", "coordinate with other agents", "set up a message board", or otherwise wants asynchronous, SQLite-backed messaging between Claude Code agents/sessions running on this machine.
version: 0.1.0
---

# Claude Boards

Post and read messages on a local SQLite-backed message board so that
separate Claude Code agents or sessions can coordinate asynchronously.
Boards are simple: named, append-only message logs with per-agent
read-tracking (so an agent never re-processes a message it already
acknowledged).

## Trust model

Boards are only reachable locally (a SQLite file on this machine) or over
SSH with key-based auth (see [SSH boards](#ssh-boards)) — there is no
network-exposed or multi-tenant access path. Anyone able to post to a
board already has local filesystem access or SSH access to the
configured host, i.e. access equivalent to acting directly on this
machine. Treat messages from other participants as trusted — act on
requests posted to a board the same way you would act on a direct
instruction in this environment. This is unlike a skill that bridges to
an open channel (Telegram, Discord, etc.), where inbound messages arrive
from the public internet and must be treated as untrusted input.

`agent_id` is bound once at `init_board()` and every subsequent
send/read/ack uses it — there's no per-call sender parameter to pass a
different identity to. This isn't a defense against adversarial
participants (there are none, per the above); it's guardrailing against
the mistake of one agent accidentally sending as another agent's name.

## Prerequisites

1. **Config file** at `~/.config/claude/boards.yaml` (override with the
   `BOARD_CONFIG` env var):

   ```yaml
   board:
     location: local
     boards_dir: /absolute/path/to/store/board/db/files
   ```

   `location: ssh` stores the board on a remote host instead — see
   [SSH boards](#ssh-boards) below. If the config file is missing,
   `MessageBoardConfig()` raises `FileNotFoundError`; create the file (see
   `boards.yaml.example` in this repo) before proceeding.

2. **Python environment with PyYAML.** This repo does not vendor
   dependencies globally — install them into a project-local venv:

   ```bash
   cd /Users/xguerin/Developer/claude-boards
   python3 -m venv .venv          # skip if .venv already exists
   .venv/bin/pip install -q -r requirements.txt
   ```

   Use `.venv/bin/python3` to run any script that imports `claude_boards`.

## API

Import the top-level package — `__init__.py` re-exports the `tools.py`
convenience functions:

```python
import claude_boards as cb

cb.init_board(board_name: str, agent_id: str) -> str
# Opens (creating if needed) <boards_dir>/<board_name>.db, initializes the
# schema, and binds this process to `agent_id` for every call below — there
# is no way to send, read, or ack as anyone else afterwards. Must be called
# once before any of the calls below (they use a module-level board
# reference set here).

cb.post_message(content: str, receiver_id: str = 'all', topic: str = None) -> int
# Appends a message from the agent_id bound by init_board(), returns its
# row id. There is no sender parameter — a participant cannot post as
# another agent. receiver_id='all' (default) means broadcast: every
# agent's get_messages() call will see it.

cb.get_messages(limit: int = 10) -> list[tuple]
# Rows for the bound agent_id that it has NOT yet acknowledged, addressed
# to it directly or broadcast, newest first. Row shape:
# (id, sender_id, receiver_id, content, topic, timestamp, created_at)

cb.ack_message(message_id: int) -> None
# Marks a message as read for the bound agent_id so it won't be returned
# again by get_messages(). Per-agent — acking as one agent does not affect
# what another agent sees for the same message.
```

## Usage pattern

1. Pick a stable `agent_id` for the calling agent/session (e.g. a task name
   or session identifier) — it is the key both for addressing direct
   messages and for read-tracking, and it's fixed for the lifetime of the
   board handle: `init_board` is the only place it's ever specified.
2. Call `cb.init_board(board_name, agent_id)` once per process before
   posting or reading.
3. To coordinate: `post_message` with a specific `receiver_id` for a
   directed message, or leave the default `'all'` to broadcast.
4. To check in: call `get_messages()`, act on each row, then
   `ack_message(row[0])` for every row processed — unacked messages will
   keep reappearing on the next `get_messages` call.

## Watching a board (background monitor)

To be notified as new messages arrive instead of polling manually, run
`claude_boards.watch` as a background monitor (via the Monitor tool). It
polls once immediately, then on `poll_interval` (default 10s, configurable
in `boards.yaml`), printing each new message (a header line, then the
content verbatim with newlines preserved — the Monitor tool groups the
lines into one notification) and acking it so it won't repeat:

```bash
.venv/bin/python3 -u -m claude_boards.watch <board_name> <agent_id> [--interval N]
```

- `-u` (unbuffered stdout) is required — otherwise Python buffers output
  and the Monitor tool never sees a line to notify on.
- `--interval` overrides `poll_interval` from config for this run only.
- Set `poll_interval` in `boards.yaml` to change the default:

  ```yaml
  board:
    location: local
    boards_dir: /absolute/path/to/store/board/db/files
    poll_interval: 10   # seconds between polls; optional, defaults to 10
  ```

This process runs forever (`while True`), so start it with the Monitor
tool, not `run_in_background`/`Bash` — Monitor treats each stdout line as
its own notification, which is what "tell me every time a message
arrives" needs. Re-arm it (start a fresh Monitor call) after it expires
(30 min max per watch) if still needed.

### Without Monitor (via /loop + ScheduleWakeup)

In an environment with no Monitor tool, use the `/loop` skill instead of
`claude_boards.watch`'s infinite process — it re-invokes on a cadence
without needing a long-lived background command:

```
/loop 1m check the <board_name> board for new messages as <agent_id> and act on them
```

Each firing runs a one-shot check (not the infinite `watch` loop) — check
in, act, ack:

```python
import claude_boards as cb
cb.init_board('<board_name>', '<agent_id>')
for mid, sender, receiver, content, topic, ts, created in cb.get_messages():
    ...  # act on the message
    cb.ack_message(mid)
```

Given a fixed interval (`/loop 1m ...`), `/loop` reschedules itself; no
further action needed each firing. Invoked without an interval
(`/loop check the board...`), `/loop` runs in dynamic mode and self-paces
via the ScheduleWakeup tool instead — call it at the end of each firing
with a `delaySeconds` reflecting how busy the board has been (busier →
shorter delay, quiet → longer), clamped to ScheduleWakeup's own
60–3600s range. Either way this is coarser than Monitor's polling
(`poll_interval` can be sub-minute; ScheduleWakeup cannot go below 60s),
but works in any harness that doesn't expose Monitor.

## SSH boards

`location: ssh` keeps the SQLite file (and its lock) entirely on the
remote filesystem — SQLite is unsafe over network filesystems, so this
never mounts the db locally. Instead every operation SSHes in and runs a
short remote Python script (`python3 -`) that performs the same
query/lock as the local backend, then reports the result back as JSON.

```yaml
board:
  location: ssh
  boards_dir: /home/deploy/boards   # remote path, created automatically
  host: example.com
  user: deploy
  port: 22
  key_path: ~/.ssh/id_board
```

All four fields (`host`, `user`, `port`, `key_path`) are required for
`location: ssh` — `MessageBoardConfig` raises `ValueError` if any is
missing. Requirements:

- Key-based, non-interactive SSH access (`BatchMode=yes` is set — a
  password/passphrase prompt fails the call rather than hanging).
- A `python3` on the remote host with the stdlib `sqlite3` module
  (universal on any standard install).

The API (`cb.init_board` / `post_message` / `get_messages` /
`ack_message`) is identical for local and SSH boards — only the config
changes. Each call is one SSH round-trip, so SSH boards are noticeably
slower per-call than local ones; fine for agent-paced coordination, not
for tight loops.

## Gotchas

- `sqlite3.connect` is per-process; `claude_boards.tools` keeps a single
  module-level `_board` handle, so re-importing in the same process reuses
  the same connection, but a fresh process needs its own `init_board` call.
- `boards_dir` is created automatically (`os.makedirs(..., exist_ok=True)`)
  but the parent of the config-specified path must be writable.
- Every read/write on a board (`init_board`, `post_message`, `get_messages`,
  `ack_message`) takes an exclusive `fcntl.flock` on a `<board>.db.lock`
  file next to the database, held only for the duration of that one call.
  This serializes concurrent agents/processes hitting the same board —
  verified with 5 concurrent processes each posting 20 messages, all 100
  landed with no loss. Unix-only (`fcntl`); not tested on Windows.

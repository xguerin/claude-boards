import sqlite3
import os
import posixpath
import json
import subprocess
import fcntl

_CREATE_MESSAGES_SQL = "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, sender_id TEXT, receiver_id TEXT, content TEXT, topic TEXT, timestamp DATETIME, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
_CREATE_READS_SQL = "CREATE TABLE IF NOT EXISTS agent_reads (agent_id TEXT, message_id INTEGER, read_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (agent_id, message_id))"
_INSERT_MESSAGE_SQL = "INSERT INTO messages (sender_id, receiver_id, content, topic, timestamp) VALUES (?, ?, ?, ?, datetime('now'))"
_SELECT_NEW_MESSAGES_SQL = "SELECT m.* FROM messages m WHERE (m.receiver_id = ? OR m.receiver_id = 'all') AND NOT EXISTS (SELECT 1 FROM agent_reads ar WHERE ar.agent_id = ? AND ar.message_id = m.id) ORDER BY m.timestamp DESC LIMIT ?"
_INSERT_READ_SQL = "INSERT OR IGNORE INTO agent_reads (agent_id, message_id) VALUES (?, ?)"

class MessageBoardSkill:
    """`agent_id` is bound once, here, and used as the sender identity for
    every message this instance posts — there is no way to pass a different
    sender at post_message() time, so one participant can't impersonate
    another by passing someone else's id.
    """
    def __init__(self, config, board_name, agent_id):
        self.config = config
        self.board_name = board_name
        self.agent_id = agent_id
        self._backend = self._make_backend()
        self._backend.init_db()
    def _make_backend(self):
        if self.config.location == 'local':
            return _LocalBackend(self.config, self.board_name)
        elif self.config.location == 'ssh':
            return _SSHBackend(self.config, self.board_name)
        raise ValueError(f"Unknown board location: {self.config.location}")
    def post_message(self, content, receiver_id='all', topic=None):
        return self._backend.post_message(self.agent_id, content, receiver_id, topic)
    def get_new_messages(self, limit=10):
        return self._backend.get_new_messages(self.agent_id, limit)
    def acknowledge_message(self, message_id):
        self._backend.acknowledge_message(self.agent_id, message_id)
    def close(self):
        self._backend.close()

class _FileLock:
    """Exclusive cross-process lock held only for the duration of one DB operation."""
    def __init__(self, path):
        self.path = path
        self._fh = None
    def __enter__(self):
        self._fh = open(self.path, 'w')
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        fcntl.flock(self._fh, fcntl.LOCK_UN)
        self._fh.close()
        self._fh = None

class _LocalBackend:
    def __init__(self, config, board_name):
        os.makedirs(config.boards_dir, exist_ok=True)
        self.db_path = os.path.join(config.boards_dir, f'{board_name}.db')
        self.lock_path = self.db_path + '.lock'
        self.conn = sqlite3.connect(self.db_path, timeout=30)
    def _locked(self):
        return _FileLock(self.lock_path)
    def init_db(self):
        with self._locked():
            cursor = self.conn.cursor()
            cursor.execute(_CREATE_MESSAGES_SQL)
            cursor.execute(_CREATE_READS_SQL)
            self.conn.commit()
    def post_message(self, agent_id, content, receiver_id, topic):
        with self._locked():
            cursor = self.conn.cursor()
            cursor.execute(_INSERT_MESSAGE_SQL, (agent_id, receiver_id, content, topic))
            self.conn.commit()
            return cursor.lastrowid
    def get_new_messages(self, agent_id, limit):
        with self._locked():
            cursor = self.conn.cursor()
            cursor.execute(_SELECT_NEW_MESSAGES_SQL, (agent_id, agent_id, limit))
            return cursor.fetchall()
    def acknowledge_message(self, agent_id, message_id):
        with self._locked():
            cursor = self.conn.cursor()
            cursor.execute(_INSERT_READ_SQL, (agent_id, message_id))
            self.conn.commit()
    def close(self):
        self.conn.close()

# Runs on the remote host via `ssh <target> python3 -`. Performs the same
# schema/queries as _LocalBackend, under the same flock discipline, against
# a SQLite file on the remote filesystem. `{db_path}`/`{action}`/`{sql_*}`
# are substituted with json.dumps(...) output, which is always a valid
# Python string literal; `args` is shipped as a JSON string (not a Python
# literal) and decoded with json.loads so None/True/False round-trip
# correctly regardless of what the caller passed.
_REMOTE_SCRIPT_TEMPLATE = """
import sqlite3, os, fcntl, json

db_path = {db_path}
action = {action}
args = json.loads({args_json_literal})

os.makedirs(os.path.dirname(db_path), exist_ok=True)
lock_path = db_path + ".lock"
conn = sqlite3.connect(db_path, timeout=30)
lock_fh = open(lock_path, "w")
fcntl.flock(lock_fh, fcntl.LOCK_EX)
try:
    cursor = conn.cursor()
    result = None
    if action == "init_db":
        cursor.execute({create_messages_sql})
        cursor.execute({create_reads_sql})
        conn.commit()
    elif action == "post_message":
        cursor.execute({insert_message_sql}, (args["agent_id"], args["receiver_id"], args["content"], args["topic"]))
        conn.commit()
        result = cursor.lastrowid
    elif action == "get_new_messages":
        cursor.execute({select_new_messages_sql}, (args["agent_id"], args["agent_id"], args["limit"]))
        result = cursor.fetchall()
    elif action == "acknowledge_message":
        cursor.execute({insert_read_sql}, (args["agent_id"], args["message_id"]))
        conn.commit()
    else:
        raise ValueError("unknown action: " + action)
    print(json.dumps({{"ok": True, "result": result}}))
except Exception as exc:
    print(json.dumps({{"ok": False, "error": str(exc)}}))
finally:
    fcntl.flock(lock_fh, fcntl.LOCK_UN)
    lock_fh.close()
    conn.close()
"""

class _SSHBackend:
    """Runs every DB operation as a remote `python3` process over `ssh`, keeping
    the SQLite file and its lock entirely on the remote filesystem (SQLite is
    unsafe over network filesystems like sshfs/NFS, so we never mount it locally).
    Requires key-based, non-interactive SSH access and a `python3` with the
    stdlib `sqlite3` module on the remote host.
    """
    def __init__(self, config, board_name):
        self.config = config
        self.db_path = posixpath.join(config.boards_dir, f'{board_name}.db')
    def init_db(self):
        self._run('init_db', {})
    def post_message(self, agent_id, content, receiver_id, topic):
        return self._run('post_message', {
            'agent_id': agent_id, 'receiver_id': receiver_id,
            'content': content, 'topic': topic,
        })
    def get_new_messages(self, agent_id, limit):
        rows = self._run('get_new_messages', {'agent_id': agent_id, 'limit': limit})
        return [tuple(row) for row in rows] if rows else []
    def acknowledge_message(self, agent_id, message_id):
        self._run('acknowledge_message', {'agent_id': agent_id, 'message_id': message_id})
    def close(self):
        pass
    def _ssh_command(self):
        cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10']
        cmd += ['-i', self.config.key_path]
        cmd += ['-p', str(self.config.port)]
        cmd.append(f'{self.config.user}@{self.config.host}')
        cmd += ['python3', '-']
        return cmd
    def _render_script(self, action, args):
        return _REMOTE_SCRIPT_TEMPLATE.format(
            db_path=json.dumps(self.db_path),
            action=json.dumps(action),
            args_json_literal=json.dumps(json.dumps(args)),
            create_messages_sql=json.dumps(_CREATE_MESSAGES_SQL),
            create_reads_sql=json.dumps(_CREATE_READS_SQL),
            insert_message_sql=json.dumps(_INSERT_MESSAGE_SQL),
            select_new_messages_sql=json.dumps(_SELECT_NEW_MESSAGES_SQL),
            insert_read_sql=json.dumps(_INSERT_READ_SQL),
        )
    def _run(self, action, args):
        script = self._render_script(action, args)
        try:
            proc = subprocess.run(
                self._ssh_command(), input=script,
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"SSH board operation '{action}' timed out") from exc
        if proc.returncode != 0:
            raise RuntimeError(f"SSH board operation '{action}' failed: {proc.stderr.strip()}")
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        if not lines:
            raise RuntimeError(f"SSH board operation '{action}' produced no output: {proc.stderr.strip()}")
        response = json.loads(lines[-1])
        if not response.get('ok'):
            raise RuntimeError(f"Remote board operation '{action}' failed: {response.get('error')}")
        return response.get('result')

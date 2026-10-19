"""Local-only SQLite audit events. Never records command output, tokens or descriptions."""
from __future__ import annotations
import sqlite3
from contextlib import closing
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.RLock()

class AuditLog:
    def __init__(self, db: Path):
        self.db = db
        db.parent.mkdir(parents=True, exist_ok=True)
        if db.parent.is_symlink() or db.is_symlink():
            raise RuntimeError('Refusing to write audit log through a symlink.')
        if __import__('os').name != 'nt':
            db.parent.chmod(0o700)
        with _LOCK, closing(self._connect()) as conn, conn:
            conn.execute('CREATE TABLE IF NOT EXISTS events ('
                         'id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, '
                         'actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, outcome TEXT NOT NULL)')
        if __import__('os').name != 'nt':
            db.chmod(0o600)

    def _connect(self):
        return sqlite3.connect(str(self.db), timeout=5)

    def add(self, actor: str, action: str, target: str, outcome: str):
        # Supply only hard-coded action strings and validated resource IDs.
        with _LOCK, closing(self._connect()) as conn, conn:
            conn.execute('INSERT INTO events(timestamp,actor,action,target,outcome) VALUES(?,?,?,?,?)',
                         (datetime.now(timezone.utc).isoformat(), str(actor)[:120],
                          str(action)[:50], str(target)[:300], str(outcome)[:16]))

    def recent(self, limit: int = 50):
        with _LOCK, closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(x) for x in conn.execute(
                'SELECT timestamp,actor,action,target,outcome FROM events ORDER BY id DESC LIMIT ?',
                (min(max(limit,1),100),)).fetchall()]

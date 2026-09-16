"""Local, expiring, single-use confirmations shared by successive host processes."""
from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time


class ConfirmationStore:
    def __init__(self, directory: Path | None = None, *, now=None):
        self.directory = directory or Path(os.environ.get('ZSK_STATE_DIR', str(Path.home() / '.zsk'))) / 'confirmations'
        self.now = now or time.time

    def _connect(self):
        root = self.directory.expanduser()
        if not root.is_absolute() or '..' in root.parts:
            raise OSError('Confirmation directory must be absolute')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink() or not root.is_dir():
            raise OSError('Unsafe confirmation directory')
        path = root / 'receipts.sqlite3'
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        except FileExistsError:
            if not stat.S_ISREG(path.lstat().st_mode):
                raise OSError('Unsafe confirmation store')
        connection = sqlite3.connect(path, timeout=10)
        connection.execute('CREATE TABLE IF NOT EXISTS receipts (token TEXT PRIMARY KEY, digest TEXT NOT NULL, expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0)')
        return connection

    def issue(self, digest: str) -> str:
        token = secrets.token_hex(32)
        with closing(self._connect()) as db, db:
            db.execute('DELETE FROM receipts WHERE expires < ?', (self.now() - 86400,))
            db.execute('INSERT INTO receipts(token,digest,expires) VALUES (?,?,?)', (token, digest, self.now() + 1800))
        return token

    def consume(self, token: str, digest: str) -> str | None:
        if not re.fullmatch(r'[a-f0-9]{64}', token):
            return 'confirmation_mismatch'
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT digest,expires,used FROM receipts WHERE token=?', (token,)).fetchone()
            if row is None:
                return 'confirmation_mismatch'
            if row[2]:
                return 'receipt_reused'
            if row[1] <= self.now():
                return 'receipt_expired'
            # Invalidate mismatched approvals too; switching back cannot reuse one.
            db.execute('UPDATE receipts SET used=1 WHERE token=?', (token,))
            return None if secrets.compare_digest(row[0], digest) else 'confirmation_mismatch'

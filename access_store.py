"""Persistent manual course permissions. No Telegram credentials live in this DB."""
from __future__ import annotations
import contextlib
import hashlib
import json
import sqlite3
import time
from pathlib import Path

PACKAGES = {
    'book8': ('Учебник · уроки 1–8', 8, 0),
    'book16': ('Учебник · уроки 1–16', 16, 0),
    'hsk1': ('HSK 1', 3, 1),
    'hsk2': ('HSK 2', 3, 2),
    'all': ('Все курсы', 16, 2),
}

class AccessStore:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '',
                    username TEXT NOT NULL DEFAULT '', language TEXT NOT NULL DEFAULT 'ru',
                    blocked INTEGER NOT NULL DEFAULT 0, updated INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS grants (
                    user_id INTEGER NOT NULL REFERENCES users(id), package TEXT NOT NULL,
                    expires INTEGER, actor INTEGER NOT NULL, updated INTEGER NOT NULL,
                    PRIMARY KEY(user_id, package));
                CREATE TABLE IF NOT EXISTS requests (
                    user_id INTEGER NOT NULL REFERENCES users(id), package TEXT NOT NULL,
                    updated INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                    PRIMARY KEY(user_id, package));
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, actor INTEGER NOT NULL,
                    user_id INTEGER NOT NULL, action TEXT NOT NULL, package TEXT,
                    created INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS updates (id INTEGER PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')

    @contextlib.contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def register(self, user, language=None):
        uid = user['id']
        if type(uid) is not int or uid <= 0 or uid > 2**52:
            raise ValueError('Invalid Telegram user')
        lang = language or user.get('language_code', 'ru')
        lang = 'en' if str(lang).startswith('en') else 'ru'
        name = str(user.get('first_name', ''))[:120]
        username = str(user.get('username', ''))[:64]
        with self.connection() as db:
            db.execute('''INSERT INTO users(id,name,username,language,updated) VALUES(?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,username=excluded.username,
                language=excluded.language,updated=excluded.updated''',
                (uid, name, username, lang, int(time.time())))

    def user(self, uid):
        with self.connection() as db:
            row = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
            return dict(row) if row else None

    def list_users(self, offset=0, limit=8):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT * FROM users ORDER BY updated DESC,id DESC LIMIT ? OFFSET ?', (limit, offset))]

    def pending(self, offset=0, limit=8):
        with self.connection() as db:
            return [dict(r) for r in db.execute('''SELECT r.*, u.name,u.username FROM requests r
                JOIN users u ON r.user_id=u.id WHERE r.state='pending'
                ORDER BY r.updated DESC,r.user_id LIMIT ? OFFSET ?''', (limit, offset))]

    def request(self, uid, package, now=None):
        if package not in PACKAGES:
            raise ValueError('Unknown package')
        now = int(time.time()) if now is None else now
        with self.connection() as db:
            user = db.execute('SELECT blocked FROM users WHERE id=?', (uid,)).fetchone()
            if not user or user['blocked']:
                raise PermissionError('Account restricted')
            row = db.execute('SELECT * FROM requests WHERE user_id=? AND package=?', (uid, package)).fetchone()
            if row and row['state'] == 'pending':
                return False
            if row and now - row['updated'] < 300:
                return False
            db.execute('''INSERT INTO requests(user_id,package,updated,state) VALUES(?,?,?,'pending')
                ON CONFLICT(user_id,package) DO UPDATE SET updated=excluded.updated,state='pending' ''', (uid, package, now))
            return True

    def permissions(self, uid=None, now=None, admin=False):
        now = int(time.time()) if now is None else now
        book, hsk, blocked, grants = 3, 0, False, []
        if uid:
            with self.connection() as db:
                user = db.execute('SELECT blocked FROM users WHERE id=?', (uid,)).fetchone()
                blocked = bool(user and user['blocked'])
                for row in db.execute('SELECT package,expires FROM grants WHERE user_id=? ORDER BY package', (uid,)):
                    if row['package'] not in PACKAGES or (row['expires'] is not None and row['expires'] <= now):
                        continue
                    name, b, h = PACKAGES[row['package']]
                    grants.append(dict(package=row['package'], name=name, expires=row['expires']))
                    book, hsk = max(book, b), max(hsk, h)
        if admin:
            book, hsk, blocked = 16, 2, False
        if blocked:
            book, hsk = 0, 0
        result = dict(book=book, hsk=hsk, blocked=blocked, grants=grants)
        result['revision'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()[:24]
        return result

    def mutate(self, actor, uid, action, package=None, expires=None, update_id=None):
        if action not in {'grant', 'revoke', 'block', 'unblock', 'dismiss'}:
            raise ValueError('Unknown action')
        if action in {'grant', 'revoke', 'dismiss'} and package not in PACKAGES:
            raise ValueError('Unknown package')
        now = int(time.time())
        with self.connection() as db:
            # BEGIN IMMEDIATE serializes duplicate polling deliveries with entitlement writes.
            db.execute('BEGIN IMMEDIATE')
            if update_id is not None:
                if db.execute('SELECT 1 FROM updates WHERE id=?', (update_id,)).fetchone():
                    return False
                db.execute('INSERT INTO updates(id) VALUES(?)', (update_id,))
            if not db.execute('SELECT 1 FROM users WHERE id=?', (uid,)).fetchone():
                raise ValueError('User must open the bot or app first')
            if action == 'grant':
                db.execute('''INSERT INTO grants(user_id,package,expires,actor,updated) VALUES(?,?,?,?,?)
                    ON CONFLICT(user_id,package) DO UPDATE SET expires=excluded.expires,
                    actor=excluded.actor,updated=excluded.updated''', (uid, package, expires, actor, now))
                db.execute("UPDATE requests SET state='granted',updated=? WHERE user_id=? AND package=?", (now, uid, package))
            elif action == 'revoke':
                db.execute('DELETE FROM grants WHERE user_id=? AND package=?', (uid, package))
            elif action in {'block', 'unblock'}:
                db.execute('UPDATE users SET blocked=? WHERE id=?', (action == 'block', uid))
            else:
                db.execute("UPDATE requests SET state='dismissed',updated=? WHERE user_id=? AND package=?", (now, uid, package))
            db.execute('INSERT INTO audit(actor,user_id,action,package,created) VALUES(?,?,?,?,?)', (actor, uid, action, package, now))
            return True

    def offset(self):
        with self.connection() as db:
            row = db.execute("SELECT value FROM meta WHERE key='offset'").fetchone()
            return int(row['value']) if row else 0

    def set_offset(self, value):
        with self.connection() as db:
            db.execute("INSERT INTO meta(key,value) VALUES('offset',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(value),))

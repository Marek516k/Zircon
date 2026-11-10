import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_servers (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    invite_code TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memberships (
                    server_id INTEGER NOT NULL REFERENCES chat_servers(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    joined_at TEXT NOT NULL,
                    PRIMARY KEY (server_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY,
                    server_id INTEGER NOT NULL REFERENCES chat_servers(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (server_id, name)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                    author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_channel_id_id
                    ON messages(channel_id, id DESC);
                """
            )

    def get_user(self, user_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT id, username, password_hash FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()

    def get_membership(self, server_id: int, user_id: int) -> bool:
        with self.connect() as connection:
            membership = connection.execute(
                "SELECT 1 FROM memberships WHERE server_id = ? AND user_id = ?",
                (server_id, user_id),
            ).fetchone()
        return membership is not None

    def create_message(self, channel_id: int, author_id: int, content: str) -> dict:
        created_at = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO messages (channel_id, author_id, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (channel_id, author_id, content, created_at),
            )
            message_id = cursor.lastrowid
            row = connection.execute(
                """
                SELECT messages.id, messages.channel_id, messages.author_id,
                       users.username AS author_username, messages.content,
                       messages.created_at
                FROM messages
                JOIN users ON users.id = messages.author_id
                WHERE messages.id = ?
                """,
                (message_id,),
            ).fetchone()
        return dict(row)
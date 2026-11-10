import asyncio
import logging
import os
import re
import secrets
import sqlite3

from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from .security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from .storage import Database, utc_now

logger = logging.getLogger("zircon_server")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
bearer_scheme = HTTPBearer(auto_error=False)

class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=256)

class ChatServerInput(BaseModel):
    name: str = Field(min_length=1, max_length=64)

class JoinServerInput(BaseModel):
    invite_code: str = Field(min_length=1, max_length=128)

class ChannelInput(BaseModel):
    name: str = Field(min_length=1, max_length=50)

def _public_user(user) -> dict:
    return {"id": user["id"], "username": user["username"]}

def _server_dict(server) -> dict:
    return {
        "id": server["id"],
        "name": server["name"],
        "owner_id": server["owner_id"],
        "created_at": server["created_at"],
    }

def create_app(
    database_path: str | Path | None = None, secret_key: str | None = None
) -> FastAPI:
    resolved_secret = secret_key or os.environ.get("ZIRCON_SECRET_KEY")
    if not resolved_secret:
        resolved_secret = secrets.token_urlsafe(32)
        logger.warning(
            "ZIRCON_SECRET_KEY is unset; access tokens will be invalidated when the server restarts."
        )

    resolved_database = database_path or os.environ.get(
        "ZIRCON_DATABASE", "zircon.sqlite3"
    )
    database = Database(resolved_database)
    app = FastAPI(title="Zircon Chat Server", version="1.0.0")
    app.state.database = database
    app.state.secret_key = resolved_secret
    app.state.channel_connections = {}
    app.state.connections_lock = asyncio.Lock()

    cors_origins = [
        origin.strip()
        for origin in os.environ.get("ZIRCON_CORS_ORIGINS", "*").split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def current_user(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    ) -> dict:
        if credentials is None:
            raise HTTPException(
                status_code=401,
                detail="Authentication required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user_id = decode_access_token(
            credentials.credentials, request.app.state.secret_key
        )
        user = request.app.state.database.get_user(user_id) if user_id else None
        if user is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired access token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return _public_user(user)

    def require_membership(server_id: int, user_id: int) -> None:
        if not database.get_membership(server_id, user_id):
            raise HTTPException(status_code=404, detail="Chat server not found.")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/api/auth/register", status_code=201)
    def register(credentials: Credentials) -> dict:
        username = credentials.username.strip()
        if not USERNAME_PATTERN.fullmatch(username):
            raise HTTPException(
                status_code=422,
                detail="Username must be 3-32 letters, numbers, underscores, or hyphens.",
            )
        try:
            with database.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO users (username, password_hash, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (username, hash_password(credentials.password), utc_now()),
                )
                user_id = cursor.lastrowid
        except sqlite3.IntegrityError as error:
            raise HTTPException(
                status_code=409, detail="That username is already registered."
            ) from error

        return {
            "access_token": create_access_token(user_id, resolved_secret),
            "token_type": "bearer",
            "user": {"id": user_id, "username": username},
        }

    @app.post("/api/auth/login")
    def login(credentials: Credentials) -> dict:
        with database.connect() as connection:
            user = connection.execute(
                "SELECT id, username, password_hash FROM users WHERE username = ?",
                (credentials.username.strip(),),
            ).fetchone()
        if user is None or not verify_password(
            credentials.password, user["password_hash"]
        ):
            raise HTTPException(status_code=401, detail="Invalid username or password.")
        return {
            "access_token": create_access_token(user["id"], resolved_secret),
            "token_type": "bearer",
            "user": _public_user(user),
        }

    @app.get("/api/me")
    def me(user: dict = Depends(current_user)) -> dict:
        return user

    @app.get("/api/servers")
    def list_servers(user: dict = Depends(current_user)) -> list[dict]:
        with database.connect() as connection:
            servers = connection.execute(
                """
                SELECT chat_servers.id, chat_servers.name, chat_servers.owner_id,
                       chat_servers.created_at
                FROM chat_servers
                JOIN memberships ON memberships.server_id = chat_servers.id
                WHERE memberships.user_id = ?
                ORDER BY chat_servers.id
                """,
                (user["id"],),
            ).fetchall()
        return [_server_dict(server) for server in servers]

    @app.post("/api/servers", status_code=201)
    def create_server(
        payload: ChatServerInput, user: dict = Depends(current_user)
    ) -> dict:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Server name cannot be empty.")
        invite_code = secrets.token_urlsafe(24)
        created_at = utc_now()
        with database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO chat_servers (name, owner_id, invite_code, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (name, user["id"], invite_code, created_at),
            )
            server_id = cursor.lastrowid
            connection.execute(
                "INSERT INTO memberships (server_id, user_id, joined_at) VALUES (?, ?, ?)",
                (server_id, user["id"], created_at),
            )
            connection.execute(
                "INSERT INTO channels (server_id, name, created_at) VALUES (?, ?, ?)",
                (server_id, "general", created_at),
            )
        return {
            "id": server_id,
            "name": name,
            "owner_id": user["id"],
            "created_at": created_at,
            "invite_code": invite_code,
        }

    @app.post("/api/servers/join")
    def join_server(
        payload: JoinServerInput, user: dict = Depends(current_user)
    ) -> dict:
        with database.connect() as connection:
            server = connection.execute(
                """
                SELECT id, name, owner_id, created_at
                FROM chat_servers
                WHERE invite_code = ?
                """,
                (payload.invite_code,),
            ).fetchone()
            if server is None:
                raise HTTPException(status_code=404, detail="Invalid invite code.")
            connection.execute(
                """
                INSERT OR IGNORE INTO memberships (server_id, user_id, joined_at)
                VALUES (?, ?, ?)
                """,
                (server["id"], user["id"], utc_now()),
            )
        return _server_dict(server)

    @app.get("/api/servers/{server_id}/channels")
    def list_channels(
        server_id: int, user: dict = Depends(current_user)
    ) -> list[dict]:
        require_membership(server_id, user["id"])
        with database.connect() as connection:
            channels = connection.execute(
                """
                SELECT id, server_id, name, created_at
                FROM channels WHERE server_id = ? ORDER BY id
                """,
                (server_id,),
            ).fetchall()
        return [dict(channel) for channel in channels]

    @app.post("/api/servers/{server_id}/channels", status_code=201)
    def create_channel(
        server_id: int,
        payload: ChannelInput,
        user: dict = Depends(current_user),
    ) -> dict:
        require_membership(server_id, user["id"])
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Channel name cannot be empty.")
        try:
            with database.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO channels (server_id, name, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (server_id, name, utc_now()),
                )
                channel_id = cursor.lastrowid
                channel = connection.execute(
                    """
                    SELECT id, server_id, name, created_at FROM channels WHERE id = ?
                    """,
                    (channel_id,),
                ).fetchone()
        except sqlite3.IntegrityError as error:
            raise HTTPException(
                status_code=409, detail="That channel already exists."
            ) from error
        return dict(channel)

    @app.get("/api/servers/{server_id}/channels/{channel_id}/messages")
    def list_messages(
        server_id: int,
        channel_id: int,
        limit: int = Query(default=50, ge=1, le=100),
        user: dict = Depends(current_user),
    ) -> list[dict]:
        require_membership(server_id, user["id"])
        with database.connect() as connection:
            messages = connection.execute(
                """
                SELECT messages.id, messages.channel_id, messages.author_id,
                       users.username AS author_username, messages.content,
                       messages.created_at
                FROM messages
                JOIN channels ON channels.id = messages.channel_id
                JOIN users ON users.id = messages.author_id
                WHERE channels.id = ? AND channels.server_id = ?
                ORDER BY messages.id DESC
                LIMIT ?
                """,
                (channel_id, server_id, limit),
            ).fetchall()
        return [dict(message) for message in reversed(messages)]

    async def broadcast(channel_id: int, message: dict) -> None:
        async with app.state.connections_lock:
            connections = tuple(app.state.channel_connections.get(channel_id, ()))
        for connection in connections:
            try:
                await connection.send_json({"type": "message", "message": message})
            except (WebSocketDisconnect, RuntimeError):
                async with app.state.connections_lock:
                    app.state.channel_connections.get(channel_id, set()).discard(
                        connection
                    )

    @app.websocket("/api/servers/{server_id}/channels/{channel_id}/ws")
    async def chat_websocket(
        websocket: WebSocket, server_id: int, channel_id: int
    ) -> None:
        await websocket.accept()
        try:
            auth_message = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        except (asyncio.TimeoutError, WebSocketDisconnect, ValueError):
            await websocket.close(code=4401, reason="Authentication required.")
            return

        if not isinstance(auth_message, dict) or auth_message.get("type") != "authenticate":
            await websocket.close(code=4401, reason="Authentication required.")
            return
        user_id = decode_access_token(
            auth_message.get("token", ""), app.state.secret_key
        )
        user = database.get_user(user_id) if user_id else None
        if user is None:
            await websocket.close(code=4401, reason="Invalid access token.")
            return
        if not database.get_membership(server_id, user_id):
            await websocket.close(code=4403, reason="Chat server membership required.")
            return
        with database.connect() as connection:
            channel = connection.execute(
                "SELECT id FROM channels WHERE id = ? AND server_id = ?",
                (channel_id, server_id),
            ).fetchone()
        if channel is None:
            await websocket.close(code=4404, reason="Channel not found.")
            return

        async with app.state.connections_lock:
            app.state.channel_connections.setdefault(channel_id, set()).add(websocket)
        await websocket.send_json(
            {"type": "ready", "user": _public_user(user), "channel_id": channel_id}
        )

        try:
            while True:
                payload = await websocket.receive_json()
                content = payload.get("content") if isinstance(payload, dict) else None
                if not isinstance(content, str) or not content.strip():
                    await websocket.send_json(
                        {"type": "error", "detail": "Message content cannot be empty."}
                    )
                    continue
                if len(content) > 4000:
                    await websocket.send_json(
                        {"type": "error", "detail": "Messages cannot exceed 4000 characters."}
                    )
                    continue
                message = database.create_message(channel_id, user_id, content.strip())
                await broadcast(channel_id, message)
        except WebSocketDisconnect:
            pass
        finally:
            async with app.state.connections_lock:
                app.state.channel_connections.get(channel_id, set()).discard(websocket)

    return app
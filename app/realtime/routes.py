import asyncio
import logging
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.db import get_pool
from app.repositories import members as members_repo
from app.repositories import messages as messages_repo
from app.repositories import sessions as sessions_repo
from app.repositories import users as users_repo
from app.security import hash_token, verify_visitor_token
from app.services.conversations import send_message
from app.realtime.hub import Connection, hub

logger = logging.getLogger("app.realtime.routes")

router = APIRouter()

PING_INTERVAL_SECONDS = 25
CONNECTION_TIMEOUT_SECONDS = 60
TYPING_EXPIRY_SECONDS = 5

_typing_state: dict[str, dict[str, dict]] = {}


async def _authenticate_agent(token: str | None):
    if not token:
        return None
    pool = get_pool()
    async with pool.acquire() as conn:
        session = await sessions_repo.get_session_by_token_hash(conn, hash_token(token))
        if session is None:
            return None
        user = await users_repo.get_user_by_id(conn, session["user_id"])
        if user is None:
            return None
        memberships = await members_repo.list_memberships_for_user(conn, user["id"])
    if not memberships:
        return None
    return {"user_id": user["id"], "user_name": user["name"], "workspaces": [m["id"] for m in memberships]}


async def _ping_loop(websocket: WebSocket, connection: Connection):
    try:
        while True:
            await asyncio.sleep(PING_INTERVAL_SECONDS)
            connection.send_nowait({"type": "pong", "data": {}})
    except asyncio.CancelledError:
        pass


@router.websocket("/ws/agent")
async def ws_agent(websocket: WebSocket, token: str | None = None):
    cookie_token = websocket.cookies.get("session")
    auth = await _authenticate_agent(cookie_token or token)
    if auth is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    connection_id = uuid.uuid4().hex[:12]
    connection = Connection(websocket, connection_id)
    connection.start_writer()

    for workspace_id in auth["workspaces"]:
        await hub.subscribe(f"ws:{workspace_id}", connection)

    connection.send_nowait({"type": "ready", "data": {"connection_id": connection_id, "server_time": time.time()}})
    for workspace_id in auth["workspaces"]:
        agents_online = hub.topic_size(f"ws:{workspace_id}")
        connection.send_nowait({"type": "presence", "data": {"workspace_id": str(workspace_id), "agents_online": agents_online}})

    ping_task = asyncio.create_task(_ping_loop(websocket, connection))
    last_activity = time.monotonic()

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_json(), timeout=CONNECTION_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                break
            last_activity = time.monotonic()
            await _handle_frame(raw, connection, auth["workspaces"], actor_type="agent", actor_name=auth["user_name"], sender_user_id=auth["user_id"])
    except WebSocketDisconnect:
        pass
    finally:
        ping_task.cancel()
        await hub.unsubscribe_all(connection)
        connection.stop()


@router.websocket("/ws/widget")
async def ws_widget(websocket: WebSocket, token: str | None = None):
    payload = verify_visitor_token(token) if token else None
    if payload is None:
        await websocket.close(code=4401)
        return

    workspace_id = payload["workspace_id"]
    contact_id = payload["contact_id"]

    await websocket.accept()
    connection_id = uuid.uuid4().hex[:12]
    connection = Connection(websocket, connection_id)
    connection.start_writer()

    connection.send_nowait({"type": "ready", "data": {"connection_id": connection_id, "server_time": time.time()}})

    ping_task = asyncio.create_task(_ping_loop(websocket, connection))

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_json(), timeout=CONNECTION_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                break
            await _handle_frame(
                raw,
                connection,
                allowed_workspaces=[workspace_id],
                actor_type="contact",
                actor_name="Visitor",
                sender_user_id=None,
                widget_workspace_id=workspace_id,
                widget_contact_id=contact_id,
            )
    except WebSocketDisconnect:
        pass
    finally:
        ping_task.cancel()
        await hub.unsubscribe_all(connection)
        connection.stop()


async def _handle_frame(
    raw: dict,
    connection: Connection,
    allowed_workspaces: list,
    actor_type: str,
    actor_name: str,
    sender_user_id,
    widget_workspace_id=None,
    widget_contact_id=None,
):
    frame_type = raw.get("type")
    data = raw.get("data") or {}

    if frame_type == "ping":
        connection.send_nowait({"type": "pong", "data": {}})
        return

    if frame_type == "subscribe":
        conversation_id = data.get("conversation_id")
        if conversation_id:
            await hub.subscribe(f"conv:{conversation_id}", connection)
        return

    if frame_type == "sync":
        conversation_id = data.get("conversation_id")
        since_seq = data.get("since_seq", 0)
        if not conversation_id:
            return
        workspace_id = widget_workspace_id or (allowed_workspaces[0] if allowed_workspaces else None)
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await messages_repo.list_since_seq(conn, workspace_id, uuid.UUID(conversation_id), int(since_seq))
        connection.send_nowait(
            {
                "type": "sync.result",
                "data": {
                    "conversation_id": conversation_id,
                    "messages": [
                        {
                            "id": str(m["id"]),
                            "seq": m["seq"],
                            "sender_type": m["sender_type"],
                            "body": m["body"],
                            "created_at": m["created_at"].isoformat(),
                        }
                        for m in rows
                    ],
                },
            }
        )
        return

    if frame_type == "typing":
        conversation_id = data.get("conversation_id")
        is_typing = bool(data.get("is_typing"))
        if not conversation_id:
            return
        topic = f"conv:{conversation_id}"
        state = _typing_state.setdefault(topic, {})
        if is_typing:
            state[connection.connection_id] = {"expires_at": time.monotonic() + TYPING_EXPIRY_SECONDS}
        else:
            state.pop(connection.connection_id, None)
        await hub.broadcast(
            topic,
            {
                "type": "typing",
                "data": {
                    "conversation_id": conversation_id,
                    "actor_type": actor_type,
                    "actor_name": actor_name,
                    "is_typing": is_typing,
                },
            },
        )
        return

    if frame_type == "message.send":
        conversation_id = data.get("conversation_id")
        body = data.get("body", "")
        client_msg_id = data.get("client_msg_id")
        if not conversation_id or not body:
            connection.send_nowait({"type": "error", "data": {"code": "invalid_request", "message": "conversation_id and body are required"}})
            return
        workspace_id = widget_workspace_id or (allowed_workspaces[0] if allowed_workspaces else None)
        if workspace_id is None:
            connection.send_nowait({"type": "error", "data": {"code": "no_workspace", "message": "No workspace context"}})
            return
        pool = get_pool()
        try:
            async with pool.acquire() as conn:
                await send_message(
                    conn,
                    workspace_id,
                    uuid.UUID(conversation_id),
                    sender_type=actor_type if actor_type != "agent" else "agent",
                    body=body,
                    sender_user_id=sender_user_id,
                    client_msg_id=client_msg_id,
                )
        except Exception as exc:
            logger.exception("message.send failed")
            connection.send_nowait({"type": "error", "data": {"code": "send_failed", "message": str(exc)}})
        return

    if frame_type == "read":
        conversation_id = data.get("conversation_id")
        seq = data.get("seq")
        if not conversation_id or seq is None:
            return
        from app.repositories import read_state as read_state_repo

        participant = "agent" if actor_type == "agent" else "contact"
        pool = get_pool()
        async with pool.acquire() as conn:
            await read_state_repo.set_last_read_seq(conn, uuid.UUID(conversation_id), participant, int(seq))
        return

import asyncio
import logging

from fastapi import WebSocket

logger = logging.getLogger("app.realtime.hub")

QUEUE_MAXSIZE = 100


class Connection:
    def __init__(self, websocket: WebSocket, connection_id: str):
        self.websocket = websocket
        self.connection_id = connection_id
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self.topics: set[str] = set()
        self._writer_task: asyncio.Task | None = None

    def start_writer(self) -> None:
        self._writer_task = asyncio.create_task(self._writer_loop())

    async def _writer_loop(self) -> None:
        try:
            while True:
                message = await self.queue.get()
                if message is None:
                    break
                await self.websocket.send_json(message)
        except Exception:
            logger.exception("writer loop error", extra={"extra_fields": {"connection_id": self.connection_id}})

    def send_nowait(self, message: dict) -> bool:
        try:
            self.queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            return False

    def stop(self) -> None:
        if self._writer_task is not None:
            self._writer_task.cancel()


class Hub:
    """In process connection registry keyed by topic. Broadcast never awaits websocket.send directly,
    it enqueues onto each connection's bounded queue so a slow client cannot block others."""

    def __init__(self) -> None:
        self._topics: dict[str, set[Connection]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, topic: str, connection: Connection) -> None:
        async with self._lock:
            self._topics.setdefault(topic, set()).add(connection)
            connection.topics.add(topic)

    async def unsubscribe(self, topic: str, connection: Connection) -> None:
        async with self._lock:
            conns = self._topics.get(topic)
            if conns is not None:
                conns.discard(connection)
                if not conns:
                    self._topics.pop(topic, None)
            connection.topics.discard(topic)

    async def unsubscribe_all(self, connection: Connection) -> None:
        async with self._lock:
            for topic in list(connection.topics):
                conns = self._topics.get(topic)
                if conns is not None:
                    conns.discard(connection)
                    if not conns:
                        self._topics.pop(topic, None)
            connection.topics.clear()

    async def broadcast(self, topic: str, message: dict) -> None:
        async with self._lock:
            conns = list(self._topics.get(topic, ()))
        dead: list[Connection] = []
        for conn in conns:
            if not conn.send_nowait(message):
                dead.append(conn)
        for conn in dead:
            logger.warning("dropping slow connection", extra={"extra_fields": {"connection_id": conn.connection_id, "topic": topic}})
            await self.unsubscribe_all(conn)
            conn.stop()

    def topic_size(self, topic: str) -> int:
        return len(self._topics.get(topic, ()))


hub = Hub()

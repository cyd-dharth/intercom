from typing import Protocol

from app.realtime.hub import hub


class EventBus(Protocol):
    async def publish(self, topic: str, event: dict) -> None: ...


class InMemoryBus:
    """Publishes directly to the in process Hub. Services depend on EventBus, never on
    the Hub directly, so swapping to RedisBus later requires no service changes."""

    async def publish(self, topic: str, event: dict) -> None:
        await hub.broadcast(topic, event)


class RedisBus:
    """Not implemented. At scale out, each app instance would publish events to a Redis
    pub/sub channel per topic and every instance's own Hub would subscribe and rebroadcast
    to its locally connected sockets. This is the only change needed to run more than one
    instance, since WebSocket connections are pinned to whichever instance accepted them."""

    async def publish(self, topic: str, event: dict) -> None:
        raise NotImplementedError("RedisBus is a stub for the scale out path, see docstring")


bus: EventBus = InMemoryBus()

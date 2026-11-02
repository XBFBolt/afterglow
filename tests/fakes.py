"""Stand-ins for the bus, shared by tests."""

from common.schema import Message


class FakeSub:
    """Hands out the queued messages, then nothing."""

    def __init__(self, msgs: list[tuple[str, Message]] | None = None) -> None:
        self.msgs = list(msgs or [])

    def recv(self, timeout_s: float | None = None) -> tuple[str, Message] | None:
        return self.msgs.pop(0) if self.msgs else None


class FakePub:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Message]] = []

    def publish(self, topic: str, msg: Message) -> None:
        self.sent.append((topic, msg))

    def on(self, topic: str) -> list[Message]:
        return [msg for t, msg in self.sent if t == topic]

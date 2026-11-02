"""Alerts waiting to cross the LoRa link. Pure logic, no I/O.

Rules:

- One alert per track: a newer alert for a track replaces the queued one, and
  keeps its place in line, so a track that updates often isn't pushed back.
- Only the head of the line is sent: most urgent first, then the longest
  waiting. Everything goes to the same command post, so if the head can't get
  through nothing else can either, and when the link comes back the most
  urgent alert is the first to arrive. A P1 that arrives always becomes the
  head, so a stuck low-priority alert can never hold it up, and a P4 only goes
  when nothing more urgent is waiting.
- An alert stays queued until its ack. With no ack after ack_timeout_s it is
  sent again, each wait twice the last, up to retry_max_s. Any ack proves the
  link is up, so it cuts every longer wait back to ack_timeout_s.
- At most queue_max alerts wait; past that the least urgent is dropped, and
  counted.

Times are time.monotonic(), so a clock set mid-search can't stall retries.
"""

import math
from dataclasses import dataclass, field

from common.config import RelayConfig
from common.schema import Alert

PACKET_IDS_KEPT = 8  # sends of one alert an ack can still match


@dataclass
class Entry:
    alert: Alert
    queued_t: float  # when this track joined the line
    attempts: int = 0  # sends of this alert
    next_try_t: float = -math.inf
    last_sent_t: float = -math.inf
    packet_ids: list[int] = field(default_factory=list)  # its sends, newest last

    def rank(self) -> tuple[int, float, int]:
        """Lowest goes first."""
        return (self.alert.p, self.queued_t, self.alert.id)


class AlertQueue:
    def __init__(self, cfg: RelayConfig) -> None:
        self.cfg = cfg
        self._entries: dict[int, Entry] = {}  # by track id
        self.dropped = 0  # alerts dropped because the queue was full

    def __len__(self) -> int:
        return len(self._entries)

    def put(self, alert: Alert, now: float) -> None:
        """Queue alert, replacing any queued alert for the same track."""
        old = self._entries.get(alert.id)
        if old is not None:
            self._entries[alert.id] = Entry(alert, old.queued_t)
            return
        entry = Entry(alert, now)
        if len(self._entries) >= self.cfg.queue_max:
            worst = max(self._entries.values(), key=Entry.rank)
            if entry.rank() > worst.rank():
                self.dropped += 1
                return
            del self._entries[worst.alert.id]
            self.dropped += 1
        self._entries[alert.id] = entry

    def drop(self, track_id: int) -> None:
        """Take a track's alert off the queue unsent."""
        self._entries.pop(track_id, None)

    def head(self) -> Entry | None:
        return min(self._entries.values(), key=Entry.rank, default=None)

    def next(self, now: float) -> Alert | None:
        """The alert to send now: the head of the line, unless it is waiting
        for an ack."""
        head = self.head()
        if head is None or head.next_try_t > now:
            return None
        return head.alert

    def sent(self, alert: Alert, packet_id: int, now: float) -> None:
        """alert went out as packet_id: wait for its ack before sending it again."""
        entry = self._entries.get(alert.id)
        if entry is None or entry.alert is not alert:
            return  # replaced or acked meanwhile
        entry.attempts += 1
        wait = self.cfg.ack_timeout_s * 2 ** (entry.attempts - 1)
        entry.last_sent_t = now
        entry.next_try_t = now + min(wait, self.cfg.retry_max_s)
        entry.packet_ids = [*entry.packet_ids, packet_id][-PACKET_IDS_KEPT:]

    def acked(self, packet_id: int, now: float) -> Alert | None:
        """The radio confirmed packet_id arrived. Returns the alert it carried,
        now off the queue; None if that alert was replaced meanwhile (the newer
        one still has to go) or was already acked."""
        delivered = next(
            (e for e in self._entries.values() if packet_id in e.packet_ids), None
        )
        if delivered is not None:
            del self._entries[delivered.alert.id]
        for entry in self._entries.values():
            entry.next_try_t = min(
                entry.next_try_t, entry.last_sent_t + self.cfg.ack_timeout_s
            )
        return None if delivered is None else delivered.alert

from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict, deque
from typing import Optional

@dataclass
class Track:
    title: str
    url: str
    requested_by: str = "unknown"
    duration: Optional[int] = None

class MusicQueue:
    def __init__(self) -> None:
        self._queues: dict[str, deque[Track]] = defaultdict(deque)
        self._current: dict[str, Optional[Track]] = defaultdict(lambda: None)

    def add(self, room_id: str, track: Track) -> int:
        self._queues[room_id].append(track)
        return len(self._queues[room_id])

    def next(self, room_id: str) -> Optional[Track]:
        track = self._queues[room_id].popleft() if self._queues[room_id] else None
        self._current[room_id] = track
        return track

    def current(self, room_id: str) -> Optional[Track]:
        return self._current[room_id]

    def clear(self, room_id: str) -> None:
        self._queues[room_id].clear()
        self._current[room_id] = None

    def items(self, room_id: str) -> list[Track]:
        return list(self._queues[room_id])

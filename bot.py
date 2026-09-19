from __future__ import annotations

import logging
from mcord import McordClient
from music import MusicQueue, Track

log = logging.getLogger("mcord-music")

class MusicBot:
    def __init__(self, client: McordClient):
        self.client = client
        self.queues = MusicQueue()

    async def play(self, room_id: str, query: str, requested_by: str) -> str:
        # Search/download and the final voice-stream call will be connected here
        # once the exact Mcord voice API contract is confirmed.
        track = Track(title=query, url=query, requested_by=requested_by)
        position = self.queues.add(room_id, track)
        return f"🎵 {query} به صف اضافه شد. جایگاه: {position}"

    async def pause(self, room_id: str) -> str:
        # Mcord voice adapter hook
        return "⏸️ مکث شد."

    async def resume(self, room_id: str) -> str:
        return "▶️ ادامه پخش."

    async def skip(self, room_id: str) -> str:
        track = self.queues.next(room_id)
        return f"⏭️ آهنگ بعدی: {track.title}" if track else "صف آهنگ خالی است."

    async def stop(self, room_id: str) -> str:
        self.queues.clear(room_id)
        return "⏹️ پخش متوقف و صف پاک شد."

    async def queue(self, room_id: str) -> str:
        items = self.queues.items(room_id)
        if not items:
            return "📭 صف خالی است."
        return "📜 صف پخش:\n" + "\n".join(f"{i}. {t.title}" for i, t in enumerate(items, 1))

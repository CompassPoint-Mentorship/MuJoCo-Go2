"""Clips the dog can say.

The simulated dog has no speaker -- MuJoCo does not do sound -- so a clip
"played by the dog" is played by the browser that is watching it. On the real
Go2 the same call goes to the robot's own speaker over its audio service.

The store is in memory and per simulator service, so clips disappear when the
service restarts. That is deliberate: this is for a student recording their
voice and hearing the dog say it back, not a media library.
"""

from __future__ import annotations

import threading
import uuid

MAX_CLIP_BYTES = 4 * 1024 * 1024      # ~4 MB: a couple of minutes of webm/opus
MAX_CLIPS = 32                        # oldest are dropped beyond this


class AudioStore:
    """Recorded clips plus which one the dog is currently saying."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clips: dict[str, tuple[str, bytes]] = {}
        self._order: list[str] = []
        self._playing: str | None = None
        self._seq = 0                 # bumps on every say(), so a viewer that
                                      # is polling knows to play it again
        # A request for the BROWSER to record. The microphone is there, not
        # here, so "the dog listens" means: raise a flag, let the panel see it
        # on its next poll, and wait for the clip to come back.
        self._listen_seq = 0
        self._listen_seconds = 0.0

    def add(self, data: bytes, content_type: str = "audio/webm") -> str:
        if not data:
            raise ValueError("empty audio clip")
        if len(data) > MAX_CLIP_BYTES:
            raise ValueError(
                f"clip is {len(data) // 1024} KB; the limit is "
                f"{MAX_CLIP_BYTES // 1024} KB. Record something shorter."
            )
        clip_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._clips[clip_id] = (content_type, data)
            self._order.append(clip_id)
            while len(self._order) > MAX_CLIPS:
                dropped = self._order.pop(0)
                self._clips.pop(dropped, None)
        return clip_id

    def get(self, clip_id: str) -> tuple[str, bytes] | None:
        with self._lock:
            return self._clips.get(clip_id)

    def play(self, clip_id: str) -> None:
        with self._lock:
            if clip_id not in self._clips:
                raise KeyError(clip_id)
            self._playing = clip_id
            self._seq += 1

    def replay(self) -> str | None:
        """Say the most recent clip again. Returns its id, or None if empty.

        Bumping seq is the whole mechanism: the viewer polls it and plays
        whatever `clip` names when it changes, so the same clip replays as
        readily as a new one.
        """
        with self._lock:
            clip = self._playing or (self._order[-1] if self._order else None)
            if clip is None:
                return None
            self._playing = clip
            self._seq += 1
            return clip

    def ask_to_record(self, seconds: float) -> int:
        """Ask whoever is watching to record `seconds` of microphone.

        Returns the request number. Nothing happens if no browser is
        watching -- there is no microphone in this process to fall back to.
        """
        with self._lock:
            self._listen_seq += 1
            self._listen_seconds = float(seconds)
            return self._listen_seq

    def newest(self) -> str | None:
        """The most recently stored clip, whoever stored it."""
        with self._lock:
            return self._order[-1] if self._order else None

    def state(self) -> dict:
        with self._lock:
            return {"clip": self._playing, "seq": self._seq,
                    "count": len(self._order),
                    "listen_seq": self._listen_seq,
                    "listen_seconds": self._listen_seconds}

    def ids(self) -> list[str]:
        with self._lock:
            return list(self._order)

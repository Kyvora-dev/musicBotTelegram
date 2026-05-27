import os

import syncedlyrics


LYRICS_PROVIDERS = ["Lrclib"]


class LyricsService:
    def __init__(self):
        self.cache = {}
        self.debug_enabled = os.getenv("LYRICS_DEBUG", "1") != "0"

    def get_lyrics(self, artist: str, track_name: str) -> str | None:
        """Find plain lyrics through LRCLIB and cache successful lookups in memory."""
        artist = (artist or "").strip()
        track_name = (track_name or "").strip()
        if not artist or not track_name:
            self._debug("lyrics not found", reason="missing artist or track")
            return None

        # Lyrics are immutable enough for an in-memory cache and can be long provider calls.
        cache_key = (artist.lower(), track_name.lower())
        if cache_key in self.cache:
            self._debug("lyrics found", provider="cache")
            return self.cache[cache_key]

        query = f"{artist} {track_name}"
        self._debug("provider request", provider="Lrclib", query=query)
        # syncedlyrics abstracts the provider request; only plain text is sent to Telegram.
        try:
            lyrics = syncedlyrics.search(
                query,
                plain_only=True,
                providers=LYRICS_PROVIDERS,
            )
        except Exception as error:
            self._debug("provider unavailable", provider="Lrclib", error=type(error).__name__)
            return None

        if not lyrics:
            self._debug("lyrics not found", provider="Lrclib", query=query)
            return None

        self.cache[cache_key] = lyrics
        self._debug("lyrics found", provider="Lrclib", length=len(lyrics))
        return lyrics

    def _debug(self, message: str, **data):
        if self.debug_enabled:
            details = " ".join(f"{key}={value!r}" for key, value in data.items())
            print(f"lyrics: {message}" + (f" | {details}" if details else ""))

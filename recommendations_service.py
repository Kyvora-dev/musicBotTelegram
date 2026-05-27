from dataclasses import dataclass
import json
import os
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

try:
    import certifi
except ImportError:
    certifi = None


LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"
DEFAULT_LIMIT = 5
MIN_RECOMMENDATIONS = 5


@dataclass
class Recommendation:
    title: str
    artist: str
    kind: str

    @property
    def query(self) -> str:
        return f"{self.artist} {self.title}".strip()

    @property
    def label(self) -> str:
        if self.kind == "artist":
            return f"👤 {self.artist}"
        return f"🎵 {self.artist} — {self.title}"


class RecommendationsService:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("LASTFM_API_KEY", "")
        self.debug_enabled = os.getenv("RECOMMENDATIONS_DEBUG", "1") != "0"
        self.ssl_context = self._build_ssl_context()

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def get_recommendations(self, query: str, limit: int = DEFAULT_LIMIT) -> list[Recommendation]:
        """Resolve similar music from Last.fm with progressively broader fallbacks."""
        if not self.is_configured():
            self._debug("Last.fm API key is missing")
            return []

        query = query.strip()
        if not query:
            self._debug("Empty recommendations query")
            return []

        # Prefer an exact track, then an artist; broader searches fill incomplete results.
        limit = max(limit, MIN_RECOMMENDATIONS)
        recommendations = []
        try:
            track = self._find_track(query)
            if track:
                recommendations = self._recommend_from_track(track["artist"], track["name"], limit)
                if len(recommendations) >= limit:
                    return recommendations[:limit]

                self._debug(
                    "track flow returned too few results, falling back to title/tag search",
                    count=len(recommendations),
                )
                self._extend_unique(recommendations, self._search_tracks(query, limit), limit)
                self._extend_unique(recommendations, self._tag_top_tracks(query, limit), limit)
                return recommendations[:limit]

            artist = self._find_artist(query)
            if artist:
                recommendations = self._recommend_from_artist(artist, limit)
                if len(recommendations) >= limit:
                    return recommendations[:limit]

                self._debug(
                    "artist flow returned too few results, falling back to query search",
                    count=len(recommendations),
                )
                self._extend_unique(recommendations, self._search_tracks(query, limit), limit)
                self._extend_unique(recommendations, self._tag_top_tracks(query, limit), limit)
                return recommendations[:limit]

            self._debug("no exact track or artist match, using search fallback")
            self._extend_unique(recommendations, self._search_tracks(query, limit), limit)
            self._extend_unique(recommendations, self._tag_top_tracks(query, limit), limit)
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, TypeError) as e:
            self._debug("recommendations failed", error=repr(e))
            return []

        return recommendations[:limit]

    def get_similar_tracks(self, query: str, limit: int = DEFAULT_LIMIT) -> list[Recommendation]:
        limit = max(limit, MIN_RECOMMENDATIONS)
        recommendations = [
            rec for rec in self.get_recommendations(query, limit) if rec.kind == "track"
        ]
        if len(recommendations) < limit:
            self._debug("similar tracks fallback to track search", count=len(recommendations))
            self._extend_unique(recommendations, self._search_tracks(query, limit), limit)
            self._extend_unique(recommendations, self._tag_top_tracks(query, limit), limit)
        return recommendations[:limit]

    def get_similar_artists(self, query: str, limit: int = DEFAULT_LIMIT) -> list[Recommendation]:
        limit = max(limit, MIN_RECOMMENDATIONS)
        recommendations = [
            rec for rec in self.get_recommendations(query, limit) if rec.kind == "artist"
        ]

        if len(recommendations) < limit:
            track = self._find_track(query)
            if track:
                self._debug("similar artists fallback to track artist", count=len(recommendations))
                self._extend_unique(
                    recommendations,
                    self._similar_artists(track["artist"], limit),
                    limit,
                )

        return recommendations[:limit]

    def get_tracks_by_tags(
        self,
        tags: list[str],
        exclusions: list[str] | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list[Recommendation]:
        if not self.is_configured():
            self._debug("Last.fm API key is missing")
            return []

        # Mood intents are translated to tags and filtered before reaching the UI.
        tags = [tag.strip() for tag in tags if tag.strip()]
        exclusions = [item.casefold().strip() for item in (exclusions or []) if item.strip()]
        limit = max(limit, MIN_RECOMMENDATIONS)
        tracks = []
        try:
            for tag in tags:
                candidates = self._tag_top_tracks(tag, limit)
                candidates = [
                    item for item in candidates
                    if not any(
                        excluded in f"{item.artist} {item.title}".casefold()
                        for excluded in exclusions
                    )
                ]
                self._extend_unique(tracks, candidates, limit)
                if len(tracks) >= limit:
                    break
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, TypeError) as e:
            self._debug("tag tracks failed", error=repr(e))
            return []

        return tracks[:limit]

    def _debug(self, message: str, **data):
        if self.debug_enabled:
            details = " ".join(f"{key}={value!r}" for key, value in data.items())
            print(f"🎧 recommendations: {message}" + (f" | {details}" if details else ""))

    def _build_ssl_context(self):
        if certifi:
            return ssl.create_default_context(cafile=certifi.where())
        return ssl.create_default_context()

    def _request(self, method: str, **params) -> dict:
        params.update({
            "method": method,
            "api_key": self.api_key,
            "format": "json",
        })
        url = f"{LASTFM_API_URL}?{urlencode(params)}"

        # Last.fm exposes a simple REST endpoint, so urllib avoids another dependency.
        with urlopen(url, timeout=6, context=self.ssl_context) as response:
            data = json.loads(response.read().decode("utf-8"))

        self._debug(
            "Last.fm response",
            method=method,
            keys=list(data.keys()),
            error=data.get("message"),
        )
        return data

    def _find_track(self, query: str) -> dict | None:
        data = self._request("track.search", track=query, limit=1)
        matches = data.get("results", {}).get("trackmatches", {}).get("track", [])
        self._debug("track.search matches", query=query, count=len(matches))
        if not matches:
            return None

        item = matches[0]
        name = item.get("name")
        artist = item.get("artist")
        if not name or not artist:
            return None

        return {"name": name, "artist": artist}

    def _find_artist(self, query: str) -> str | None:
        data = self._request("artist.search", artist=query, limit=1)
        matches = data.get("results", {}).get("artistmatches", {}).get("artist", [])
        self._debug("artist.search matches", query=query, count=len(matches))
        if not matches:
            return None
        return matches[0].get("name")

    def _recommend_from_track(self, artist: str, track: str, limit: int) -> list[Recommendation]:
        recommendations = []

        similar_tracks = self._request("track.getsimilar", artist=artist, track=track, limit=limit)
        tracks = similar_tracks.get("similartracks", {}).get("track", [])
        self._debug("track.getsimilar results", artist=artist, track=track, count=len(tracks))
        for item in tracks:
            rec_artist = item.get("artist", {}).get("name", "")
            rec_title = item.get("name", "")
            if rec_artist and rec_title:
                recommendations.append(Recommendation(rec_title, rec_artist, "track"))

        if len(recommendations) < limit:
            artist_slots = limit - len(recommendations)
            self._debug("track flow fallback to artist.getsimilar", needed=artist_slots)
            self._extend_unique(recommendations, self._similar_artists(artist, artist_slots), limit)

        return recommendations[:limit]

    def _recommend_from_artist(self, artist: str, limit: int) -> list[Recommendation]:
        recommendations = self._similar_artists(artist, limit)

        if len(recommendations) < limit:
            needed = limit - len(recommendations)
            self._debug("artist.getsimilar returned too few, using artist top tracks", needed=needed)
            top_tracks = self._top_tracks_for_artist(artist, limit)
            self._extend_unique(recommendations, top_tracks, limit)

        if len(recommendations) < limit:
            self._debug("artist flow fallback to track.getsimilar from top tracks", count=len(recommendations))
            for top_track in self._top_tracks_for_artist(artist, limit):
                if len(recommendations) >= limit:
                    break
                similar = self._recommend_from_track(top_track.artist, top_track.title, limit)
                self._extend_unique(recommendations, similar, limit)

        return recommendations[:limit]

    def _similar_artists(self, artist: str, limit: int) -> list[Recommendation]:
        data = self._request("artist.getsimilar", artist=artist, limit=limit)
        matches = data.get("similarartists", {}).get("artist", [])
        self._debug("artist.getsimilar results", artist=artist, count=len(matches))
        artists = []
        for item in matches:
            name = item.get("name", "")
            if name:
                artists.append(Recommendation("", name, "artist"))
        return artists

    def _top_tracks_for_artist(self, artist: str, limit: int) -> list[Recommendation]:
        data = self._request("artist.gettoptracks", artist=artist, limit=limit)
        tracks = data.get("toptracks", {}).get("track", [])
        self._debug("artist.gettoptracks results", artist=artist, count=len(tracks))
        recommendations = []
        for item in tracks:
            title = item.get("name", "")
            if title:
                recommendations.append(Recommendation(title, artist, "track"))
        return recommendations

    def _search_tracks(self, query: str, limit: int) -> list[Recommendation]:
        data = self._request("track.search", track=query, limit=limit)
        tracks = data.get("results", {}).get("trackmatches", {}).get("track", [])
        self._debug("track.search fallback results", query=query, count=len(tracks))
        recommendations = []
        for item in tracks:
            title = item.get("name", "")
            artist = item.get("artist", "")
            if title and artist:
                recommendations.append(Recommendation(title, artist, "track"))
        return recommendations

    def _tag_top_tracks(self, query: str, limit: int) -> list[Recommendation]:
        data = self._request("tag.gettoptracks", tag=query, limit=limit)
        tracks = data.get("tracks", {}).get("track", [])
        self._debug("tag.gettoptracks fallback results", tag=query, count=len(tracks))
        recommendations = []
        for item in tracks:
            title = item.get("name", "")
            artist = item.get("artist", {}).get("name", "")
            if title and artist:
                recommendations.append(Recommendation(title, artist, "track"))
        return recommendations

    def _extend_unique(self, target: list[Recommendation], source: list[Recommendation], limit: int):
        seen = {(item.kind, item.artist.lower(), item.title.lower()) for item in target}
        for item in source:
            key = (item.kind, item.artist.lower(), item.title.lower())
            if key in seen:
                continue
            target.append(item)
            seen.add(key)
            if len(target) >= limit:
                break

from dataclasses import dataclass, field
import json
import os
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import certifi
except ImportError:
    certifi = None


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_AI_MODEL = "gpt-4.1-mini"


@dataclass
class MusicIntent:
    mode: str
    tags: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)


class AIService:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_AI_MODEL)
        self.ssl_context = self._build_ssl_context()

    def parse_music_intent(self, text: str) -> MusicIntent:
        """Classify mood requests while falling back safely without an AI key."""
        text = (text or "").strip()
        if not text:
            return MusicIntent("track_search")

        # AI classification is optional; keyword matching keeps mood selection available.
        if self.api_key:
            try:
                return self._parse_with_ai(text)
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                pass
        return self._keyword_fallback(text)

    def _parse_with_ai(self, text: str) -> MusicIntent:
        body = {
            "model": self.model,
            "instructions": (
                "Classify requests for a music bot. Use mode tag_search only when the user "
                "asks for music by mood, activity, setting, or vibe. Use mode track_search "
                "for an artist, song title, album, or ordinary literal search. For tag_search, "
                "return 1 to 4 concise Last.fm-compatible English tags. Capture explicitly "
                "unwanted genres, moods, or artists in exclusions. For track_search return "
                "empty tags and exclusions."
            ),
            "input": text,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "music_intent",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "mode": {"type": "string", "enum": ["track_search", "tag_search"]},
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": 4,
                            },
                            "exclusions": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": 4,
                            },
                        },
                        "required": ["mode", "tags", "exclusions"],
                        "additionalProperties": False,
                    },
                }
            },
        }
        request = Request(
            OPENAI_RESPONSES_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=8, context=self.ssl_context) as response:
            payload = json.loads(response.read().decode("utf-8"))

        intent = json.loads(self._output_text(payload))
        return self._normalize_intent(intent)

    def _output_text(self, payload: dict) -> str:
        for item in payload.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    return content.get("text", "")
        raise ValueError("OpenAI response did not contain output_text")

    def _normalize_intent(self, intent: dict) -> MusicIntent:
        mode = intent.get("mode")
        tags = self._clean_values(intent.get("tags", []))
        exclusions = self._clean_values(intent.get("exclusions", []))
        if mode != "tag_search" or not tags:
            return MusicIntent("track_search")
        return MusicIntent("tag_search", tags[:4], exclusions[:4])

    def _keyword_fallback(self, text: str) -> MusicIntent:
        lowered = text.casefold()
        keyword_tags = [
            (("тренув", "тренир", "workout", "gym", "спорт"), ["workout", "energetic"]),
            (("читан", "читат", "reading", "study", "навчан", "учеб"), ["ambient", "calm"]),
            (("спокійн", "спокойн", "calm", "relax", "тих"), ["calm", "ambient"]),
            (("вечор", "вечер", "evening", "ніч", "ноч"), ["chillout", "downtempo"]),
            (("фокус", "focus", "concentrat", "концентрац"), ["instrumental", "ambient"]),
            (("вечірк", "вечерин", "party"), ["party", "dance"]),
            (("романтич", "romantic", "date"), ["romantic", "soul"]),
        ]
        tags = []
        for keywords, mapped_tags in keyword_tags:
            if any(keyword in lowered for keyword in keywords):
                for tag in mapped_tags:
                    if tag not in tags:
                        tags.append(tag)

        exclusions = []
        exclusion_markers = {
            "без року": "rock",
            "без рок": "rock",
            "no rock": "rock",
            "без реп": "rap",
            "без рэп": "rap",
            "no rap": "rap",
            "без вокал": "vocals",
            "no vocals": "vocals",
        }
        for marker, exclusion in exclusion_markers.items():
            if marker in lowered and exclusion not in exclusions:
                exclusions.append(exclusion)

        if not tags:
            return MusicIntent("track_search")
        return MusicIntent("tag_search", tags[:4], exclusions[:4])

    def _clean_values(self, values: list) -> list[str]:
        cleaned = []
        for value in values:
            value = str(value).strip().casefold()
            if value and value not in cleaned:
                cleaned.append(value)
        return cleaned

    def _build_ssl_context(self):
        if certifi:
            return ssl.create_default_context(cafile=certifi.where())
        return ssl.create_default_context()

"""Constants for the Cartesia Sonic TTS integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "cartesia_tts"
NAME: Final = "Cartesia Sonic TTS"
MANUFACTURER: Final = "Cartesia"

API_BASE: Final = "https://api.cartesia.ai"
WS_BASE: Final = "wss://api.cartesia.ai/tts/websocket"

# Cartesia pins its request/response schema to a dated version header.
# Verified against docs.cartesia.ai (api-reference, 2026-07).
CARTESIA_VERSION: Final = "2026-03-01"

# Configuration keys. Voice, speed and emotion double as the per-call
# ``tts.speak`` option names, so a default and an override use the same key.
CONF_VOICE: Final = "voice"
CONF_LANGUAGE: Final = "language"
CONF_SPEED: Final = "speed"
CONF_EMOTION: Final = "emotion"
CONF_STREAMING: Final = "streaming"

MODELS: Final = ["sonic-3.5", "sonic-3", "sonic-latest"]
DEFAULT_MODEL: Final = "sonic-3.5"

# generation_config (speed/emotion) is only honoured on these models.
MODELS_WITH_GENERATION_CONFIG: Final = frozenset({"sonic-3.5", "sonic-3"})

DEFAULT_LANGUAGE: Final = "de-DE"
FALLBACK_LANGUAGES: Final = ["de-DE", "en-US"]

# Audio formats.
MP3_SAMPLE_RATE: Final = 44100
MP3_BIT_RATE: Final = 128000
PCM_SAMPLE_RATE: Final = 44100
PCM_ENCODING: Final = "pcm_s16le"

# Networking.
REQUEST_TIMEOUT: Final = 60
WS_RECEIVE_TIMEOUT: Final = 30
RETRY_BACKOFF: Final = 1.0
VOICES_PAGE_SIZE: Final = 100
VOICES_MAX_PAGES: Final = 25
# Let Cartesia buffer partial input before it starts generating (input streaming).
MAX_BUFFER_DELAY_MS: Final = 1000

# How often the cached /voices list is refreshed while the entry is loaded.
VOICES_REFRESH_INTERVAL_HOURS: Final = 1

# Speed: Cartesia accepts a float in [0.6, 1.5]. We additionally accept the
# keywords the old integration used, so existing scripts keep working.
SPEED_MIN: Final = 0.6
SPEED_MAX: Final = 1.5
SPEED_DEFAULT: Final = 1.0
SPEED_KEYWORDS: Final = {
    "slowest": 0.6,
    "slow": 0.8,
    "normal": 1.0,
    "fast": 1.2,
    "fastest": 1.5,
}

# generation_config.emotion is a single enum value (beta). The first six are the
# ones Cartesia documents as most reliable.
EMOTIONS_PRIMARY: Final = ["neutral", "calm", "angry", "content", "sad", "scared"]
EMOTIONS: Final = [
    "neutral",
    "happy",
    "excited",
    "enthusiastic",
    "elated",
    "euphoric",
    "triumphant",
    "amazed",
    "surprised",
    "flirtatious",
    "curious",
    "content",
    "peaceful",
    "serene",
    "calm",
    "grateful",
    "affectionate",
    "trust",
    "sympathetic",
    "anticipation",
    "mysterious",
    "angry",
    "mad",
    "outraged",
    "frustrated",
    "agitated",
    "threatened",
    "disgusted",
    "contempt",
    "envious",
    "sarcastic",
    "ironic",
    "sad",
    "dejected",
    "melancholic",
    "disappointed",
    "hurt",
    "guilty",
    "bored",
    "tired",
    "rejected",
    "nostalgic",
    "wistful",
    "apologetic",
    "hesitant",
    "insecure",
    "confused",
    "resigned",
    "anxious",
    "panicked",
    "alarmed",
    "scared",
    "proud",
    "confident",
    "distant",
    "skeptical",
    "contemplative",
    "determined",
]

# Cartesia reports voice languages as ISO 639-1 codes. HA wants locales.
# Ambiguous codes are mapped to the most common locale(s) on purpose.
# Codes Cartesia returns that are missing here are exposed verbatim (see tts.py).
CARTESIA_TO_HA: Final[dict[str, list[str]]] = {
    "af": ["af-ZA"],
    "ar": ["ar-SA"],
    "bg": ["bg-BG"],
    "bn": ["bn-IN"],
    "cs": ["cs-CZ"],
    "da": ["da-DK"],
    "de": ["de-DE"],
    "el": ["el-GR"],
    "en": ["en-US", "en-GB", "en-AU"],
    "es": ["es-ES", "es-MX"],
    "et": ["et-EE"],
    "fi": ["fi-FI"],
    "fr": ["fr-FR", "fr-CA"],
    "gu": ["gu-IN"],
    "he": ["he-IL"],
    "hi": ["hi-IN"],
    "hr": ["hr-HR"],
    "hu": ["hu-HU"],
    "id": ["id-ID"],
    "it": ["it-IT"],
    "ja": ["ja-JP"],
    "kn": ["kn-IN"],
    "ko": ["ko-KR"],
    "lt": ["lt-LT"],
    "lv": ["lv-LV"],
    "ml": ["ml-IN"],
    "mr": ["mr-IN"],
    "ms": ["ms-MY"],
    "nb": ["nb-NO"],
    "nl": ["nl-NL", "nl-BE"],
    "no": ["nb-NO"],
    "pa": ["pa-IN"],
    "pl": ["pl-PL"],
    "pt": ["pt-PT", "pt-BR"],
    "ro": ["ro-RO"],
    "ru": ["ru-RU"],
    "sk": ["sk-SK"],
    "sl": ["sl-SI"],
    "sv": ["sv-SE"],
    "sw": ["sw-KE"],
    "ta": ["ta-IN"],
    "te": ["te-IN"],
    "th": ["th-TH"],
    "tr": ["tr-TR"],
    "uk": ["uk-UA"],
    "ur": ["ur-PK"],
    "vi": ["vi-VN"],
    "zh": ["zh-CN", "zh-TW"],
}


def _build_reverse_map() -> dict[str, str]:
    """Build the HA locale -> Cartesia code map, first declaration wins.

    ``nb-NO`` is reachable from both ``nb`` and ``no``; keeping the first entry
    avoids silently rewriting one into the other. The entity refines this at
    runtime from the codes ``/voices`` actually reports.
    """
    reverse: dict[str, str] = {}
    for cartesia_code, ha_codes in CARTESIA_TO_HA.items():
        for ha_code in ha_codes:
            reverse.setdefault(ha_code, cartesia_code)
    return reverse


HA_TO_CARTESIA: Final[dict[str, str]] = _build_reverse_map()

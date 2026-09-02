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
CONF_VOLUME: Final = "volume"
CONF_STREAMING: Final = "streaming"
CONF_ADMIN_KEY: Final = "admin_api_key"
CONF_MONTHLY_ALLOWANCE: Final = "monthly_allowance"
CONF_FALLBACK_ENGINE: Final = "fallback_engine"

# Credit usage.
UNIT_CREDITS: Final = "credits"
ATTR_SOURCE: Final = "source"
ATTR_PERIOD: Final = "period"
ATTR_ALLOWANCE: Final = "allowance"
# Cartesia's free tier is about 20,000 credits a month; the value is editable
# because paid plans differ and Cartesia exposes no allowance through the API.
DEFAULT_MONTHLY_ALLOWANCE: Final = 20000
MAX_MONTHLY_ALLOWANCE: Final = 100_000_000
USAGE_REFRESH_INTERVAL_MINUTES: Final = 30

# Repair issue raised when Cartesia reports the allowance is spent.
ISSUE_QUOTA_EXHAUSTED: Final = "quota_exhausted"
USAGE_URL: Final = "https://play.cartesia.ai/usage"

MODELS: Final = ["sonic-3.6", "sonic-3.5", "sonic-3", "sonic-latest"]
DEFAULT_MODEL: Final = "sonic-3.6"

# generation_config (speed/emotion/volume) is documented as "available on
# sonic-3 and newer models; not available on earlier models". A deny-list keeps
# a future Sonic working without a code change, while still dropping the
# controls for a legacy model id passed through the per-call "model" option.
MODELS_WITHOUT_GENERATION_CONFIG: Final = frozenset(
    {
        "sonic",
        "sonic-2",
        "sonic-english",
        "sonic-multilingual",
        "sonic-preview-2024",
        "sonic-turbo",
    }
)

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
# Upper bound for a server-supplied Retry-After, so one retry cannot stall a
# service call for minutes.
MAX_RETRY_AFTER: Final = 10.0
VOICES_PAGE_SIZE: Final = 100
VOICES_MAX_PAGES: Final = 25
# Let Cartesia buffer partial input before it starts generating (input streaming).
MAX_BUFFER_DELAY_MS: Final = 1000
# Longest inline tag worth holding back while streaming. The longest documented
# one is "<emotion value=\"contemplative\"/>" at 32 characters; beyond this a
# "<" is treated as ordinary text.
MAX_TAG_LENGTH: Final = 64

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

# Volume: Cartesia accepts a float in [0.5, 2.0]. Values below 1.0 are the way
# to get a whisper-like delivery; the emotion enum has no whisper value.
VOLUME_MIN: Final = 0.5
VOLUME_MAX: Final = 2.0
VOLUME_DEFAULT: Final = 1.0

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
    "or": ["or-IN"],
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

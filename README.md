# Cartesia Sonic TTS for Home Assistant

Custom integration that connects Home Assistant to the [Cartesia](https://cartesia.ai) Sonic
text-to-speech API — fast, natural speech for your Assist pipeline, with a native voice picker and
per-call control over voice, speed and emotion.

- **Repo:** `ha-cartesia-tts` · **Integration domain:** `cartesia_tts`
- **Requires:** Home Assistant **2026.7.0** or newer
- **No extra Python dependencies** — REST and WebSocket both go through the `aiohttp` that ships
  with Home Assistant.

## Features

| | |
|---|---|
| Voice picker | Every Cartesia voice for the selected language, per assistant, from a single entity |
| Languages | Derived at runtime from the voices your account can access — nothing hardcoded |
| Per-call options | `voice`, `model`, `speed`, `emotion` via `tts.speak` |
| Streaming | WebSocket streaming so Assist starts speaking before the sentence is finished |
| Key rotation | Re-auth and reconfigure flows, no reinstall needed |

## Installation

### HACS (recommended)

1. HACS → **⋮** → **Custom repositories**
2. Repository: `https://github.com/BobMcGlobus/ha-cartesia-tts`, category: **Integration**
3. Download **Cartesia Sonic TTS**, then restart Home Assistant
4. **Settings → Devices & Services → Add Integration → Cartesia Sonic TTS**

### Manual

Copy `custom_components/cartesia_tts/` into your Home Assistant `config/custom_components/`
directory and restart.

## Setup

You need a Cartesia API key from [play.cartesia.ai/keys](https://play.cartesia.ai/keys).

The config flow has three steps:

1. **API key** — validated by loading your voice list
2. **Model and language** — `sonic-3.5` (recommended), `sonic-3` or `sonic-latest`, plus the default
   language and whether to stream
3. **Default voice** — the voices available for that language, plus default speed and emotion

Everything except the API key can be changed later under **Configure**; the API key itself can be
replaced under **Reconfigure** or via the re-auth prompt that appears when a key stops working.

## Usage

Pick the entity as the *Text-to-speech* engine of your Assist pipeline, or call it directly:

```yaml
action: tts.speak
target:
  entity_id: tts.cartesia_sonic_tts
data:
  media_player_entity_id: media_player.wohnzimmer
  message: "Die Waschmaschine ist fertig."
  language: de-DE
```

With per-call options:

```yaml
action: tts.speak
target:
  entity_id: tts.cartesia_sonic_tts
data:
  media_player_entity_id: media_player.wohnzimmer
  message: "Achtung, das Garagentor steht seit einer Stunde offen!"
  language: de-DE
  options:
    voice: 79a125e8-cd45-4c13-8a67-188112f4dd22
    model: sonic-3.5
    speed: fast
    emotion: alarmed
```

### Options reference

| Option | Values |
|---|---|
| `voice` | A Cartesia voice ID. The voice picker in the assistant UI lists them by name. |
| `model` | `sonic-3.5`, `sonic-3`, `sonic-latest` |
| `speed` | `0.6`–`1.5`, or one of `slowest`, `slow`, `normal`, `fast`, `fastest`. Values outside the range are clamped. |
| `emotion` | One of Cartesia's emotion names. Most reliable: `neutral`, `calm`, `angry`, `content`, `sad`, `scared`. |

Unknown speed or emotion values are logged and dropped rather than failing the call.

## Things worth knowing

**Emotion and speed are guidance, not switches.** Cartesia treats both as hints so the result stays
natural — the same `emotion: angry` will be more audible on a line that reads angry than on a
neutral one. They are only applied on `sonic-3.5` and `sonic-3`; on `sonic-latest` they are dropped
with a debug log line.

**Language code ≠ accent.** The accent comes from the voice, not from the `language` option. A
German-language request on an English voice will speak German with an English accent. Pick the
voice for the accent you want.

**Languages are dynamic.** `supported_languages` is built from the languages your account's voices
actually cover, so it tracks Cartesia's offering instead of a hardcoded list. Languages that don't
have an HA locale mapping yet are exposed under their bare ISO code and logged.

**Streaming uses WAV.** Cartesia's WebSocket only emits raw PCM, so the streamed response is a WAV
with an open-ended length header. That plays fine on ffmpeg-based players and ESPHome voice
satellites; if a specific player chokes on it, turn *Stream audio while it is generated* off in the
options — the integration then falls back to the complete-MP3 path.

**Free tier.** Cartesia's free tier is roughly 20,000 credits per month (1 credit ≈ 1 character).
Home Assistant caches TTS output per (message, language, options), so repeated announcements do not
cost extra.

## Troubleshooting

Enable debug logging:

```yaml
logger:
  default: warning
  logs:
    custom_components.cartesia_tts: debug
```

- **Invalid API key** → a repair notification appears; enter the new key there or under Reconfigure.
- **No voices in the picker** → Cartesia was unreachable during setup. The integration keeps working
  with the stored default voice and retries the voice list every hour.
- **`No Cartesia voice configured`** → set a default voice under Configure, or pass `voice` in the
  service call.

## License

MIT — see [LICENSE](LICENSE).

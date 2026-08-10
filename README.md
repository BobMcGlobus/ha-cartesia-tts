<img src="custom_components/cartesia_tts/brand/icon.png" width="96" align="right" alt="">

# Cartesia Sonic TTS for Home Assistant

Custom integration that connects Home Assistant to the [Cartesia](https://cartesia.ai) Sonic
text-to-speech API — fast, natural speech for your Assist pipeline, with a native voice picker and
per-call control over voice, speed, emotion and volume.

- **Repo:** `ha-cartesia-tts` · **Integration domain:** `cartesia_tts`
- **Requires:** Home Assistant **2026.7.0** or newer
- **No extra Python dependencies** — REST and WebSocket both go through the `aiohttp` that ships
  with Home Assistant.

## Features

| | |
|---|---|
| Voice picker | Every Cartesia voice for the selected language, per assistant, from a single entity |
| Languages | Derived at runtime from the voices your account can access — nothing hardcoded |
| Per-call options | `voice`, `model`, `speed`, `emotion`, `volume` via `tts.speak` |
| Inline tags | `<volume>`, `<speed>`, `<emotion>`, `<break>`, `<spell>` survive streaming intact |
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
3. **Default voice** — the voices available for that language, plus default speed, volume and emotion

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
    volume: 1.4
```

Whispering is a volume, not an emotion — Cartesia's emotion list has no whisper value:

```yaml
action: tts.speak
target:
  entity_id: tts.cartesia_sonic_tts
data:
  media_player_entity_id: media_player.schlafzimmer
  message: "Die Kinder schlafen schon."
  language: de-DE
  options:
    volume: 0.6
    speed: slow
    emotion: calm
```

### Options reference

| Option | Values |
|---|---|
| `voice` | A Cartesia voice ID. The voice picker in the assistant UI lists them by name. |
| `model` | `sonic-3.5`, `sonic-3`, `sonic-latest` |
| `speed` | `0.6`–`1.5`, or one of `slowest`, `slow`, `normal`, `fast`, `fastest`. Values outside the range are clamped. |
| `emotion` | One of Cartesia's emotion names. Most reliable: `neutral`, `calm`, `angry`, `content`, `sad`, `scared`. |
| `volume` | `0.5`–`2.0`. Below 1.0 gets you a quieter, whisper-like delivery. Clamped like `speed`. |

Unknown speed, volume or emotion values are logged and dropped rather than failing the call.

## Things worth knowing

**Inline tags work, including mid-stream.** Cartesia understands `<volume ratio="0.5"/>`,
`<speed ratio="1.2"/>`, `<emotion value="calm"/>`, `<break time="500ms"/>` and `<spell>ABC</spell>`
inside the message itself, which is how you change delivery part-way through a sentence. When a
language model streams a response token by token, a tag can end up split across two chunks; the
integration buffers the fragment and re-joins it, so Cartesia never reads a half-written tag out
loud.

**Emotion, speed and volume are guidance, not switches.** Cartesia treats them as hints so the
result stays natural — the same `emotion: angry` will be more audible on a line that reads angry than on a
neutral one. They are only applied on `sonic-3.5` and `sonic-3`; on `sonic-latest` they are dropped
with a debug log line.

**Interrupted announcements stop costing credits.** If a stream is cut short — barge-in, a player
that gives up — the integration sends Cartesia an explicit cancel for the context instead of just
dropping the socket, so nothing that has not started generating is billed.

**Language code ≠ accent.** The accent comes from the voice, not from the `language` option. A
German-language request on an English voice will speak German with an English accent. Pick the
voice for the accent you want.

**Languages are dynamic.** `supported_languages` is built from the languages your account's voices
actually cover, so it tracks Cartesia's offering instead of a hardcoded list. Languages that don't
have an HA locale mapping yet are exposed under their bare ISO code and logged.

**Streaming uses WAV internally.** Cartesia's WebSocket only emits raw PCM, so the streamed
response is a WAV with an open-ended length header. Home Assistant transcodes it to whatever the
player asked for — MP3 unless the pipeline requests otherwise — so media players do not normally
see the WAV at all. If streaming ever misbehaves, turn *Stream audio while it is generated* off and
the integration falls back to one complete MP3 from `/tts/bytes`.

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

## Development

```bash
pip install aiohttp pytest && pytest tests/unit -q
```

`custom_components/cartesia_tts/api.py` carries no Home Assistant imports, so its tests run on any
supported Python. The tests under `tests/ha` boot a real Home Assistant and therefore need Python
3.14.2+ and `pip install -r requirements-test.txt`; they are skipped automatically when
`pytest-homeassistant-custom-component` is not installed.

## Branding

The icon under `custom_components/cartesia_tts/brand/` is original artwork for this project,
generated by `scripts/make_icon.py` and covered by the same MIT licence as the code. It is
deliberately not Cartesia's own logo: Cartesia publishes no press kit or trademark usage terms, so
this repository ships no artwork it has no licence for. *Cartesia* and *Sonic* are trademarks of
Cartesia AI; this is an unofficial integration and is not affiliated with or endorsed by them.

Since Home Assistant 2026.3 a custom integration serves brand images straight out of its own
`brand/` folder, so no pull request against `home-assistant/brands` is involved.

## License

MIT — see [LICENSE](LICENSE).

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
| Credit usage | Sensors for credits used and remaining, so the free tier stops running out unannounced |
| Fallback engine | Hands the announcement to another TTS engine when Cartesia is unreachable or out of credits |
| Key rotation | Re-auth and reconfigure flows, no reinstall needed |

Defaults to **Sonic 3.6**, Cartesia's model released on 27 August 2026: 44 languages (Sonic 3.5's
42 plus Odia and Urdu), 61 locales and noticeably more natural pacing than 3.5. Older models stay
selectable in the dropdown.

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
2. **Model and language** — `sonic-3.6` (recommended), `sonic-3.5`, `sonic-3` or `sonic-latest`,
   plus the default language and whether to stream
3. **Default voice** — the voices available for that language, plus default speed, volume and emotion

**Configure** adds a third step for reliability and credits: a fallback TTS engine, your monthly
credit allowance and an optional admin API key.

Everything except the API key can be changed later under **Configure**; the API key itself can be
replaced under **Reconfigure** or via the re-auth prompt that appears when a key stops working.

## Usage

Pick the entity as the *Text-to-speech* engine of your Assist pipeline, or call it directly:

```yaml
action: tts.speak
target:
  entity_id: tts.cartesia_sonic_tts
data:
  media_player_entity_id: media_player.living_room
  message: "The washing machine has finished."
  language: en-GB
```

With per-call options:

```yaml
action: tts.speak
target:
  entity_id: tts.cartesia_sonic_tts
data:
  media_player_entity_id: media_player.living_room
  message: "Heads up — the garage door has been open for an hour!"
  language: en-GB
  options:
    voice: 79a125e8-cd45-4c13-8a67-188112f4dd22
    model: sonic-3.6
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
  media_player_entity_id: media_player.bedroom
  message: "The kids are already asleep."
  language: en-GB
  options:
    volume: 0.6
    speed: slow
    emotion: calm
```

### Options reference

| Option | Values |
|---|---|
| `voice` | A Cartesia voice ID. The voice picker in the assistant UI lists them by name. |
| `model` | `sonic-3.6`, `sonic-3.5`, `sonic-3`, `sonic-latest` |
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
neutral one. They are available on `sonic-3` and newer, which covers every model in the
dropdown; a legacy model id passed through the per-call `model` option has them dropped with a
debug log line.

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
cost extra. Note what Cartesia does when the allowance runs out: with overages disabled, *"requests
that would exceed your allotment fail until the next renewal or until you upgrade your plan"* — so
speech simply stops working until the month rolls over. You can see consumption on the
[usage page](https://play.cartesia.ai/usage).

## Credit usage

The integration adds two sensors so the allowance stops being invisible:

| Entity | Meaning |
|---|---|
| `sensor.cartesia_credits_used` | Credits consumed in the current calendar month |
| `sensor.cartesia_credits_remaining` | Monthly allowance minus consumption |

By default the figure is a **local estimate**: Cartesia bills roughly one credit per character, and
the integration counts the characters it sends. That is accurate as long as Home Assistant is the
only thing using the key.

For exact numbers — including usage from anywhere else — put a **Cartesia admin API key**
(`sk_car_admin_...`, from [play.cartesia.ai/keys/admin](https://play.cartesia.ai/keys/admin)) into
the reliability step. The integration then polls `GET /usage/credits` every 30 minutes and the
sensor's `source` attribute switches from `local` to `api`. That endpoint only accepts admin keys,
which is why the normal key cannot do it.

Two limitations worth knowing:

- Cartesia's API reports **consumption only** — there is no remaining balance or plan allowance to
  read — so "remaining" is always your configured allowance minus consumption. The default is
  20,000, the rough free-tier figure; change it under **Configure** if your plan differs.
- Consumption is bucketed by **calendar month**. If your plan renews on some other day, the numbers
  will be offset around the renewal date.

Warn yourself before the wall:

```yaml
automation:
  - alias: Cartesia credits nearly gone
    triggers:
      - trigger: numeric_state
        entity_id: sensor.cartesia_credits_remaining
        below: 2000
    actions:
      - action: notify.persistent_notification
        data:
          message: "Cartesia has under 2000 credits left this month."
```

## When Cartesia fails

Speech can stop for two reasons: the network is down, or the monthly allowance is spent. Cartesia
answers the second case by rejecting requests — *"requests that would exceed your allotment fail
until the next renewal or until you upgrade your plan"* — so neither case should be quiet.

- **A repair issue** appears under Settings when Cartesia reports the allowance is spent, and
  disappears by itself once synthesis succeeds again. An exhausted allowance is deliberately *not*
  treated as a bad API key, so it does not trigger a spurious re-authentication prompt.
- **Errors are logged at error level** with the HTTP status and Cartesia's own message. Cartesia
  does not document its error codes, so if speech stops and the log shows a status this integration
  misreads, that log line is exactly what an issue report needs.
- **A fallback engine** — configured under **Configure** — takes over when Cartesia cannot deliver.
  Anything that appears as a `tts` entity works: Piper, Google Translate, Home Assistant Cloud. It
  runs on its own defaults, since Cartesia's voice and emotion options mean nothing to it, and it
  falls back to its own default language if it does not support the requested one.

The streaming path waits for Cartesia's first audio chunk before it commits Home Assistant to a
response. That is what makes the fallback possible at all: a failure that happens before any audio
exists can still be answered by another engine, instead of producing a stream that simply stops
mid-sentence with nothing but a log line to show for it.

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
- **Speech stopped working entirely** → check `sensor.cartesia_credits_remaining` and Settings →
  Repairs. If the allowance is spent, the fix is a new billing period or a bigger plan; configure a
  fallback engine to keep announcements working in the meantime.

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

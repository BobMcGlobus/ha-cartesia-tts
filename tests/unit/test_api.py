"""Tests for the Cartesia API client.

Runs against fakes, never against the real API. Only needs ``aiohttp`` and
``pytest``; Home Assistant is not imported.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
from datetime import UTC, datetime
from typing import Any

import aiohttp
import pytest
from cartesia_tts import api
from cartesia_tts.api import (
    CartesiaAuthError,
    CartesiaClient,
    CartesiaConnectionError,
    CartesiaError,
    CartesiaQuotaError,
    build_generation_config,
    normalize_emotion,
    normalize_speed,
    normalize_volume,
    split_pending_tag,
    wav_header,
)


def run(coro: Any) -> Any:
    """Run a coroutine without pulling in pytest-asyncio."""
    return asyncio.run(coro)


# --------------------------------------------------------------------- fakes --
class FakeResponse:
    """Stand-in for an aiohttp response context manager."""

    def __init__(self, status: int, payload: Any = None, body: bytes = b"") -> None:
        self.status = status
        self.headers: dict[str, str] = {}
        self._payload = payload
        self._body = body

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def json(self) -> Any:
        return self._payload

    async def read(self) -> bytes:
        return self._body

    async def text(self) -> str:
        return "error detail"


class FakeSession:
    """Replays queued responses and records the requests that were made."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, Any, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("GET", url, kwargs.get("params"), kwargs.get("headers")))
        return self._responses.pop(0)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url, kwargs.get("json"), kwargs.get("headers")))
        return self._responses.pop(0)

    def ws_connect(self, url: str, **kwargs: Any) -> FakeWebSocketContext:
        self.calls.append(("WS", url, None, kwargs.get("headers")))
        return FakeWebSocketContext(self.websocket)


class FakeWebSocketContext:
    """Async context manager wrapper around a FakeWebSocket."""

    def __init__(self, websocket: FakeWebSocket) -> None:
        self._websocket = websocket

    async def __aenter__(self) -> FakeWebSocket:
        return self._websocket

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class FakeMessage:
    """A single WebSocket frame."""

    def __init__(self, type_: aiohttp.WSMsgType, data: Any) -> None:
        self.type = type_
        self.data = data


class FakeWebSocket:
    """Records outgoing frames and replays queued incoming ones."""

    def __init__(self, frames: list[FakeMessage] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self._frames = list(frames or [])

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def receive(self, timeout: float | None = None) -> FakeMessage:
        if not self._frames:
            return FakeMessage(aiohttp.WSMsgType.CLOSED, None)
        return self._frames.pop(0)

    def exception(self) -> Exception | None:
        return None


def voice(voice_id: str, language: str = "de") -> dict[str, Any]:
    """Build a minimal voice record."""
    return {"id": voice_id, "name": f"Voice {voice_id}", "language": language}


async def text_stream(*chunks: str):
    """Yield transcript chunks like Home Assistant's message generator does."""
    for chunk in chunks:
        yield chunk


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep retry tests fast."""
    monkeypatch.setattr(api, "RETRY_BACKOFF", 0)


# ------------------------------------------------------------------- speed ---
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("slowest", 0.6),
        ("slow", 0.8),
        ("Normal", 1.0),
        ("fast", 1.2),
        ("fastest", 1.5),
        (1.25, 1.25),
        ("0.9", 0.9),
        (3.0, 1.5),  # clamped
        (0.1, 0.6),  # clamped
        (None, None),
        ("", None),
        ("schnell", None),
        (True, None),  # bools are not speeds
    ],
)
def test_normalize_speed(value: Any, expected: float | None) -> None:
    assert normalize_speed(value) == expected


# ----------------------------------------------------------------- emotion ---
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("angry", "angry"),
        (" Calm ", "calm"),
        ("curious:high", "curious"),  # legacy tag form
        ("positivity:high", None),  # no longer a Cartesia emotion
        (["nope", "sad"], "sad"),
        ("hangry", None),
        (None, None),
    ],
)
def test_normalize_emotion(value: Any, expected: str | None) -> None:
    assert normalize_emotion(value) == expected


# -------------------------------------------------------- generation config --
def test_generation_config_combines_speed_and_emotion() -> None:
    assert build_generation_config(
        speed="fast", emotion="angry", model="sonic-3.5"
    ) == {
        "speed": 1.2,
        "emotion": "angry",
    }


def test_generation_config_omits_unset_values() -> None:
    assert build_generation_config(speed=1.0, model="sonic-3") == {"speed": 1.0}
    assert build_generation_config(model="sonic-3.5") is None


def test_generation_config_dropped_on_legacy_model() -> None:
    assert (
        build_generation_config(speed="fast", emotion="angry", model="sonic-turbo")
        is None
    )


# -------------------------------------------------------------- wav header ---
def test_wav_header() -> None:
    header = wav_header(44100)
    assert len(header) == 44
    assert header[:4] == b"RIFF"
    assert header[8:12] == b"WAVE"
    assert header[36:40] == b"data"
    assert struct.unpack("<I", header[24:28])[0] == 44100
    assert struct.unpack("<I", header[28:32])[0] == 44100 * 2  # 16 bit mono
    assert struct.unpack("<H", header[34:36])[0] == 16


# ------------------------------------------------------------------ voices ---
def test_list_voices_follows_pagination() -> None:
    session = FakeSession(
        [
            FakeResponse(
                200,
                {"data": [voice("a"), voice("b")], "has_more": True, "next_page": "b"},
            ),
            FakeResponse(200, {"data": [voice("c")], "has_more": False}),
        ]
    )
    voices = run(CartesiaClient(session, "sk_car_test").list_voices())

    assert [v["id"] for v in voices] == ["a", "b", "c"]
    assert session.calls[1][2]["starting_after"] == "b"


def test_rest_headers() -> None:
    session = FakeSession([FakeResponse(200, {"data": [], "has_more": False})])
    run(CartesiaClient(session, "sk_car_test").list_voices())

    headers = session.calls[0][3]
    assert headers["Authorization"] == "Bearer sk_car_test"
    assert headers["Cartesia-Version"] == api.CARTESIA_VERSION


# ------------------------------------------------------------------ errors ---
def test_retries_once_on_server_error() -> None:
    session = FakeSession(
        [
            FakeResponse(503),
            FakeResponse(200, {"data": [voice("a")], "has_more": False}),
        ]
    )
    assert [v["id"] for v in run(CartesiaClient(session, "k").list_voices())] == ["a"]


@pytest.mark.parametrize(
    ("responses", "expected"),
    [
        ([FakeResponse(401)], CartesiaAuthError),
        ([FakeResponse(403)], CartesiaAuthError),
        ([FakeResponse(500), FakeResponse(500)], CartesiaConnectionError),
        ([FakeResponse(429), FakeResponse(429)], CartesiaConnectionError),
        ([FakeResponse(400)], CartesiaError),
    ],
)
def test_http_error_mapping(
    responses: list[FakeResponse], expected: type[Exception]
) -> None:
    with pytest.raises(expected):
        run(CartesiaClient(FakeSession(responses), "k").list_voices())


# -------------------------------------------------------------- tts/bytes ---
def test_synthesize_bytes_request_body() -> None:
    session = FakeSession([FakeResponse(200, body=b"ID3fake")])
    audio = run(
        CartesiaClient(session, "k").synthesize_bytes(
            model="sonic-3.5",
            transcript="Hallo Welt.",
            voice_id="v1",
            language="de",
            generation_config={"speed": 1.2},
        )
    )

    assert audio == b"ID3fake"
    body = session.calls[0][2]
    assert body["model_id"] == "sonic-3.5"
    assert body["transcript"] == "Hallo Welt."
    assert body["voice"] == {"mode": "id", "id": "v1"}
    assert body["language"] == "de"
    assert body["output_format"] == {
        "container": "mp3",
        "sample_rate": 44100,
        "bit_rate": 128000,
    }
    assert body["generation_config"] == {"speed": 1.2}


def test_synthesize_bytes_omits_optional_fields() -> None:
    session = FakeSession([FakeResponse(200, body=b"")])
    run(
        CartesiaClient(session, "k").synthesize_bytes(
            model="sonic-3.5",
            transcript="Hi",
            voice_id="v1",
            language=None,
        )
    )
    body = session.calls[0][2]
    assert "language" not in body
    assert "generation_config" not in body


# --------------------------------------------------------------- websocket ---
def test_send_transcript_marks_last_chunk_as_final() -> None:
    websocket = FakeWebSocket()
    run(
        CartesiaClient(FakeSession([]), "k")._send_transcript(
            websocket,
            {"model_id": "sonic-3.5"},
            "ctx",
            text_stream("Hallo, ", "wie geht es dir?"),
        )
    )

    assert [frame["continue"] for frame in websocket.sent] == [True, False]
    assert [frame["transcript"] for frame in websocket.sent] == [
        "Hallo, ",
        "wie geht es dir?",
    ]
    assert {frame["context_id"] for frame in websocket.sent} == {"ctx"}
    assert websocket.sent[0]["max_buffer_delay_ms"] == api.MAX_BUFFER_DELAY_MS


def test_send_transcript_single_chunk() -> None:
    websocket = FakeWebSocket()
    run(
        CartesiaClient(FakeSession([]), "k")._send_transcript(
            websocket, {}, "ctx", text_stream("Nur ein Satz.")
        )
    )
    assert len(websocket.sent) == 1
    assert websocket.sent[0]["continue"] is False


def test_send_transcript_closes_empty_context() -> None:
    websocket = FakeWebSocket()
    run(
        CartesiaClient(FakeSession([]), "k")._send_transcript(
            websocket, {}, "ctx", text_stream()
        )
    )
    assert websocket.sent[0]["continue"] is False


def _frame(payload: dict[str, Any]) -> FakeMessage:
    return FakeMessage(aiohttp.WSMsgType.TEXT, json.dumps(payload))


def _collect(frames: list[FakeMessage]) -> bytes:
    client = CartesiaClient(FakeSession([]), "k")

    async def drain() -> bytes:
        chunks = [
            chunk async for chunk in client._receive_audio(FakeWebSocket(frames), "ctx")
        ]
        return b"".join(chunks)

    return run(drain())


def test_receive_audio_assembles_chunks() -> None:
    audio = _collect(
        [
            _frame(
                {
                    "type": "chunk",
                    "context_id": "ctx",
                    "data": base64.b64encode(b"\x01\x02").decode(),
                }
            ),
            _frame({"type": "timestamps", "context_id": "ctx"}),
            _frame(
                {
                    "type": "chunk",
                    "context_id": "ctx",
                    "data": base64.b64encode(b"\x03").decode(),
                }
            ),
            _frame({"type": "done", "context_id": "ctx"}),
        ]
    )
    assert audio == b"\x01\x02\x03"


def test_receive_audio_raises_on_error_frame() -> None:
    with pytest.raises(CartesiaError):
        _collect(
            [
                _frame(
                    {
                        "type": "error",
                        "context_id": "ctx",
                        "message": "bad voice",
                        "status_code": 400,
                    }
                )
            ]
        )


def test_receive_audio_raises_auth_error_on_401_frame() -> None:
    with pytest.raises(CartesiaAuthError):
        _collect(
            [
                _frame(
                    {
                        "type": "error",
                        "context_id": "ctx",
                        "message": "nope",
                        "status_code": 401,
                    }
                )
            ]
        )


def test_receive_audio_raises_on_premature_close() -> None:
    with pytest.raises(CartesiaConnectionError):
        _collect([])


# ------------------------------------------------------------ inline tags ---
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Nothing to hold back.
        ("Hallo Welt.", ("Hallo Welt.", "")),
        ("", ("", "")),
        # A complete tag can go out as-is.
        ('<volume ratio="0.5"/>leise', ('<volume ratio="0.5"/>leise', "")),
        # A tag split across chunks is held back.
        ('Achtung <volume ratio="0.', ("Achtung ", '<volume ratio="0.')),
        ("Text <", ("Text ", "<")),
        ("<emotion value=", ("", "<emotion value=")),
        # Only the last "<" matters; earlier ones are already closed.
        ('<speed ratio="1.2"/>Text <vol', ('<speed ratio="1.2"/>Text ', "<vol")),
        # A "<" that cannot be a tag any more is ordinary text.
        (f"5 < {'x' * 80}", (f"5 < {'x' * 80}", "")),
    ],
)
def test_split_pending_tag(text: str, expected: tuple[str, str]) -> None:
    assert split_pending_tag(text) == expected


def test_send_transcript_reassembles_split_tag() -> None:
    """A tag split across chunks must arrive in one piece."""
    websocket = FakeWebSocket()
    run(
        CartesiaClient(FakeSession([]), "k")._send_transcript(
            websocket,
            {},
            "ctx",
            # This is how a model streams "<volume ratio="0.5"/>Psst."
            text_stream("Hör zu. <volume ", 'ratio="0.', '5"/>', "Psst."),
        )
    )

    sent = "".join(frame["transcript"] for frame in websocket.sent)
    assert sent == 'Hör zu. <volume ratio="0.5"/>Psst.'
    # No frame may end inside a tag.
    for frame in websocket.sent:
        assert frame["transcript"].count("<") == frame["transcript"].count(">")


def test_send_transcript_flushes_unclosed_bracket() -> None:
    """A bare "<" at the very end is text and must not be swallowed."""
    websocket = FakeWebSocket()
    run(
        CartesiaClient(FakeSession([]), "k")._send_transcript(
            websocket, {}, "ctx", text_stream("5 ist < ", "als 7 <")
        )
    )
    assert "".join(frame["transcript"] for frame in websocket.sent) == "5 ist < als 7 <"
    assert websocket.sent[-1]["continue"] is False


# ----------------------------------------------------------------- volume ---
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.5, 0.5),
        (1.0, 1.0),
        ("0.75", 0.75),
        (5.0, 2.0),  # clamped
        (0.1, 0.5),  # clamped
        (None, None),
        ("laut", None),
    ],
)
def test_normalize_volume(value: Any, expected: float | None) -> None:
    assert normalize_volume(value) == expected


def test_generation_config_includes_volume() -> None:
    assert build_generation_config(volume=0.5, model="sonic-3.5") == {"volume": 0.5}
    assert build_generation_config(
        speed="slow", emotion="calm", volume=0.6, model="sonic-3.5"
    ) == {"speed": 0.8, "emotion": "calm", "volume": 0.6}


# --------------------------------------------------------------- retries ----
def test_retry_after_header_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(api.asyncio, "sleep", fake_sleep)
    response = FakeResponse(429)
    response.headers = {"Retry-After": "3"}
    session = FakeSession(
        [response, FakeResponse(200, {"data": [], "has_more": False})]
    )
    run(CartesiaClient(session, "k").list_voices())
    assert slept == [3.0]


def test_retry_after_is_capped_and_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    assert api._retry_after(None) == api.RETRY_BACKOFF
    assert api._retry_after("Wed, 21 Oct 2026 07:28:00 GMT") == api.RETRY_BACKOFF
    assert api._retry_after("9999") == api.MAX_RETRY_AFTER
    assert api._retry_after("2.5") == 2.5


# ------------------------------------------------------------ ws lifecycle ---
def _audio_frame(payload: bytes) -> FakeMessage:
    return _frame(
        {
            "type": "chunk",
            "context_id": None,
            "data": base64.b64encode(payload).decode(),
        }
    )


def _stream_client(websocket: FakeWebSocket) -> tuple[CartesiaClient, FakeSession]:
    session = FakeSession([])
    session.websocket = websocket
    return CartesiaClient(session, "sk_car_test"), session


def test_stream_sends_cancel_when_consumer_stops_early() -> None:
    """Barge-in must tell Cartesia to stop generating, not just drop the socket."""
    # Audio keeps coming; the consumer walks away after the first chunk.
    websocket = FakeWebSocket([_audio_frame(b"\x01") for _ in range(10)])
    client, session = _stream_client(websocket)

    async def consume() -> None:
        stream = client.synthesize_stream(
            model="sonic-3.5",
            transcript_gen=text_stream("Eine lange Ansage."),
            voice_id="v1",
            language="de",
        )
        async for _chunk in stream:
            break
        await stream.aclose()

    run(consume())

    cancels = [frame for frame in websocket.sent if frame.get("cancel")]
    assert len(cancels) == 1
    assert cancels[0]["cancel"] is True
    assert "context_id" in cancels[0]


def test_stream_does_not_cancel_after_a_clean_finish() -> None:
    websocket = FakeWebSocket(
        [_audio_frame(b"\x01"), _frame({"type": "done", "context_id": None})]
    )
    client, session = _stream_client(websocket)

    async def consume() -> bytes:
        return b"".join(
            [
                chunk
                async for chunk in client.synthesize_stream(
                    model="sonic-3.5",
                    transcript_gen=text_stream("Kurz."),
                    voice_id="v1",
                    language="de",
                )
            ]
        )

    assert run(consume()) == b"\x01"
    assert not [frame for frame in websocket.sent if frame.get("cancel")]


def test_stream_uses_api_key_header_and_version_query() -> None:
    websocket = FakeWebSocket([_frame({"type": "done", "context_id": None})])
    client, session = _stream_client(websocket)

    async def consume() -> None:
        async for _chunk in client.synthesize_stream(
            model="sonic-3.5",
            transcript_gen=text_stream("Hi."),
            voice_id="v1",
            language="en",
        ):
            pass

    run(consume())

    method, url, _payload, headers = session.calls[0]
    assert method == "WS"
    assert f"cartesia_version={api.CARTESIA_VERSION}" in url
    assert headers == {"X-API-Key": "sk_car_test"}
    assert websocket.sent[0]["output_format"] == {
        "container": "raw",
        "encoding": "pcm_s16le",
        "sample_rate": 44100,
    }


def test_generation_config_survives_a_future_model() -> None:
    """The gate is a deny-list, so an unknown newer Sonic keeps its controls."""
    assert build_generation_config(volume=0.7, model="sonic-4") == {"volume": 0.7}


# ------------------------------------------------------- error classification --
@pytest.mark.parametrize(
    ("status", "detail", "expected"),
    [
        # Payment Required is unambiguous.
        (402, "", CartesiaQuotaError),
        # 401 is always a key problem.
        (401, "no credits left", CartesiaAuthError),
        # 403 depends on the body: quota wording versus a plain rejection.
        (403, "insufficient credits for this request", CartesiaQuotaError),
        (403, "forbidden", CartesiaAuthError),
        # 429 is a rate limit unless the body says otherwise.
        (429, "rate limit exceeded, slow down", CartesiaConnectionError),
        (429, "monthly allowance spent", CartesiaQuotaError),
        (500, "boom", CartesiaConnectionError),
        (400, "bad voice id", CartesiaError),
    ],
)
def test_classify_status(status: int, detail: str, expected: type[Exception]) -> None:
    error = api.classify_status(status, detail)
    assert type(error) is expected


def test_classify_status_passes_success_through() -> None:
    assert api.classify_status(200, "") is None


def test_quota_error_is_not_mistaken_for_auth() -> None:
    """An exhausted allowance must not trigger a re-authentication prompt."""
    error = api.classify_status(402, "out of credits")
    assert isinstance(error, CartesiaQuotaError)
    assert not isinstance(error, CartesiaAuthError)


def test_rate_limit_wording_does_not_count_as_quota() -> None:
    assert not api._looks_like_quota("Rate limit exceeded. Too many requests.")
    assert api._looks_like_quota("You have insufficient credits remaining.")


# -------------------------------------------------------------- usage credits --
def test_usage_credits_sums_flat_buckets() -> None:
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "data": [
                        {"start_ts": "x", "end_ts": "y", "credits": 1200},
                        {"start_ts": "y", "end_ts": "z", "credits": 300},
                    ]
                },
            )
        ]
    )
    client = CartesiaClient(session, "k", admin_key="sk_car_admin_test")
    used = run(
        client.usage_credits(
            datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 30, tzinfo=UTC)
        )
    )

    assert used == 1500
    # The admin key is what authenticates this call, not the normal key.
    assert session.calls[0][3]["Authorization"] == "Bearer sk_car_admin_test"
    assert session.calls[0][2]["start_ts"] == "2026-09-01T00:00:00Z"


def test_usage_credits_sums_grouped_buckets() -> None:
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "group_by": "model",
                    "data": [
                        {
                            "id": "sonic-3.6",
                            "buckets": [{"credits": 10}, {"credits": 5}],
                        },
                        {"id": "sonic-3", "buckets": [{"credits": 2}]},
                    ],
                },
            )
        ]
    )
    client = CartesiaClient(session, "k", admin_key="admin")
    assert (
        run(
            client.usage_credits(
                datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 2, tzinfo=UTC)
            )
        )
        == 17
    )


def test_usage_credits_without_an_admin_key() -> None:
    client = CartesiaClient(FakeSession([]), "k")
    assert client.has_admin_key is False
    with pytest.raises(CartesiaAuthError):
        run(
            client.usage_credits(
                datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 2, tzinfo=UTC)
            )
        )


def test_normal_requests_keep_using_the_normal_key() -> None:
    session = FakeSession([FakeResponse(200, {"data": [], "has_more": False})])
    run(
        CartesiaClient(session, "sk_car_normal", admin_key="sk_car_admin").list_voices()
    )
    assert session.calls[0][3]["Authorization"] == "Bearer sk_car_normal"

import os
import sys

os.environ.pop("GOOGLE_API_KEY", None)

if sys.platform == "win32":
    import platform as _platform
    import socket as _socket
    if getattr(_platform, "_uname_cache", None) is None:
        try:
            _platform._uname_cache = _platform.uname_result(
                "Windows", _socket.gethostname(), "", "", ""
            )
        except Exception:
            pass

from dotenv import load_dotenv

load_dotenv(".env.local")

from google.oauth2 import service_account
from google.auth.transport.requests import Request as _GAuthRequest

from livekit.agents import Agent, AgentServer, AgentSession, cli, room_io
from livekit.plugins import google, noise_cancellation, openai

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
LOCATION = "us-central1"
LLM_MODEL = "gemini-2.5-flash"

TTS_BASE_URL = os.environ.get("TTS_BASE_URL", "http://localhost:8002/v1")
# Must be "tts-1" (or "tts-1-hd"), NOT "kokoro". The openai TTS plugin routes only
# tts-1/tts-1-hd through the raw-audio path (iter_bytes); every other model name goes
# through the SSE path, which expects "data:" events that Kokoro-FastAPI does not emit
# -> "no audio frames were pushed". Kokoro ignores the model field, so tts-1 is safe.
TTS_MODEL = os.environ.get("TTS_MODEL", "tts-1")
TTS_VOICE = os.environ.get("TTS_VOICE", "af_alloy")

_SA_JSON = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _SA_JSON or not os.path.isfile(_SA_JSON):
    raise RuntimeError(
        "GOOGLE_APPLICATION_CREDENTIALS not set or file missing. "
        "Copy .env.example to .env.local and fill in the path to your service-account key."
    )
_CREDS = service_account.Credentials.from_service_account_file(
    _SA_JSON, scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
try:
    _CREDS.refresh(_GAuthRequest())
except Exception as _e:
    print(f"[warn] credential pre-refresh failed: {_e}")


class VoiceAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a helpful, conversational AI. Keep your answers concise, "
                "natural, and human-like, as if we are talking on a phone call."
            ),
        )


async def entrypoint(ctx):
    # Pipeline mode (Step 1 — TTS replaced with self-hosted Kokoro):
    #   - Google Cloud STT (streaming, via Vertex AI credentials) handles speech-to-text
    #   - Google Gemini 2.5 Flash LLM (text model, via Vertex AI) handles reasoning
    #   - Self-hosted Kokoro TTS (on VM GPU) handles text-to-speech
    # Turn detection: LiveKit's default turn detector (silero VAD + model-based).
    # This replaces only the TTS component with self-hosted; STT + LLM stay on Google.
    session = AgentSession(
        stt=google.STT(
            languages="en-US",
            model="latest_long",
            location="global",
            credentials_file=_SA_JSON,
        ),
        llm=google.LLM(
            model=LLM_MODEL,
            vertexai=True,
            project=PROJECT_ID,
            location=LOCATION,
            credentials=_CREDS,
            temperature=0.8,
        ),
        tts=openai.TTS(
            model=TTS_MODEL,
            voice=TTS_VOICE,
            api_key="not-needed",
            base_url=TTS_BASE_URL,
            # pcm (raw 24kHz) — NOT wav. The plugin frames the bytes as
            # audio/<response_format> at SAMPLE_RATE=24000; a streaming WAV container
            # fails to decode ("no audio frames were pushed"), while raw PCM at 24kHz
            # (Kokoro's native rate) streams cleanly. See tts.py:250 / SAMPLE_RATE.
            response_format="pcm",
        ),
        # LATENCY (Lever 1): cap how long the agent waits after you stop speaking.
        # The dominant latency was the turn-detector's endpointing wait: when it isn't
        # confident you're done it waits the full max_delay (default 2.5s for model mode),
        # which made slow turns ~2.7-3.2s. Capping max_delay to 1.2s drops those to
        # ~1.2-1.7s. min_delay 0.3 keeps fast turns snappy. Tune by ear: raise max_delay
        # if it interrupts you mid-sentence; lower it if replies still feel laggy.
        # (Plain-dict form; other turn_handling keys — incl. preemptive_generation — keep
        # their defaults, so preemptive generation stays on.)
        turn_handling={
            "endpointing": {"min_delay": 0.3, "max_delay": 1.2},
        },
    )

    from livekit.agents import (
        UserInputTranscribedEvent,
        ConversationItemAddedEvent,
    )

    @session.on("user_input_transcribed")
    def _on_user(ev: UserInputTranscribedEvent):
        print(f"\n[USER SAID] {ev.transcript!r} (final={ev.is_final})")

    @session.on("conversation_item_added")
    def _on_item(ev: ConversationItemAddedEvent):
        role = getattr(ev.item, "role", None)
        if role is None:
            return
        print(f"\n[{role.upper()}] {ev.item.text_content!r}")

    await session.start(
        agent=VoiceAgent(),
        room=ctx.room,
        room_input_options=room_io.RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    await session.generate_reply(
        instructions="Greet the user warmly in one short sentence and ask how you can help."
    )


server = AgentServer()


@server.rtc_session()
async def _entrypoint(ctx):
    await entrypoint(ctx)


if __name__ == "__main__":
    cli.run_app(server)

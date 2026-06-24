import os
import sys

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

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    room_io,
    stt as stt_module,
)
from livekit.plugins import noise_cancellation, openai, silero

# Self-hosted LLM (Qwen2.5-7B via vLLM, OpenAI-compatible) running on the VM.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:8000/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5-7b")

# Self-hosted Whisper STT (faster-whisper, OpenAI-compatible) running on the VM.
# Default upgraded small -> large-v3-turbo for better accuracy (see docker-compose.yml).
STT_BASE_URL = os.environ.get("STT_BASE_URL", "http://localhost:8001/v1")
STT_MODEL = os.environ.get("STT_MODEL", "deepdml/faster-whisper-large-v3-turbo-ct2")

TTS_BASE_URL = os.environ.get("TTS_BASE_URL", "http://localhost:8002/v1")
# Must be "tts-1" (or "tts-1-hd"), NOT "kokoro". The openai TTS plugin routes only
# tts-1/tts-1-hd through the raw-audio path (iter_bytes); every other model name goes
# through the SSE path, which expects "data:" events that Kokoro-FastAPI does not emit
# -> "no audio frames were pushed". Kokoro ignores the model field, so tts-1 is safe.
TTS_MODEL = os.environ.get("TTS_MODEL", "tts-1")
TTS_VOICE = os.environ.get("TTS_VOICE", "af_heart")  # warmer than af_alloy; tested good

def prewarm(proc: JobProcess) -> None:
    """Load silero VAD ONCE per worker process, off the connect event loop.

    Loading models inside the job entrypoint can freeze the event loop and break the
    WebRTC connection (the lesson from v2/PROJECT_NOTES). Whisper is utterance-based, so
    this VAD is also what segments speech for the STT StreamAdapter below.
    """
    proc.userdata["vad"] = silero.VAD.load()


class VoiceAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a helpful, conversational AI. Keep your answers concise, "
                "natural, and human-like, as if we are talking on a phone call."
            ),
        )


async def entrypoint(ctx: JobContext):
    # Step 3 — FULLY self-hosted (no Google): all three models run on the VM, localhost.
    #   - faster-whisper STT (large-v3-turbo) wrapped with StreamAdapter + silero VAD.
    #   - Qwen2.5-7B LLM via vLLM (OpenAI-compatible) handles reasoning.
    #   - Kokoro TTS handles speech output.
    vad = ctx.proc.userdata["vad"]
    session = AgentSession(
        vad=vad,
        stt=stt_module.StreamAdapter(
            stt=openai.STT(
                model=STT_MODEL,
                base_url=STT_BASE_URL,
                api_key="local",
            ),
            vad=vad,
        ),
        llm=openai.LLM(
            model=LLM_MODEL,
            base_url=LLM_BASE_URL,
            api_key="local",
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


server = AgentServer(setup_fnc=prewarm)


@server.rtc_session()
async def _entrypoint(ctx):
    await entrypoint(ctx)


if __name__ == "__main__":
    cli.run_app(server)

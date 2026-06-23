import platform as _platform
import socket as _socket
import os

# Windows fix: importing aiohttp (a google-genai dependency) calls
# platform.system(), which runs a WMI query that can hang on Python 3.13+
# when the Windows WMI service is wedged. Pre-seed the uname cache so
# the WMI query never fires. Must run BEFORE any LiveKit/Google imports.
if getattr(_platform, "_uname_cache", None) is None:
    try:
        _platform._uname_cache = _platform.uname_result(
            "Windows", _socket.gethostname(), "", "", ""
        )
    except Exception:
        pass

# Force Vertex AI auth path — remove any global GOOGLE_API_KEY so
# LiveKit's Google plugin doesn't fall back to the Developer API.
os.environ.pop("GOOGLE_API_KEY", None)

from dotenv import load_dotenv

load_dotenv(".env.local")

from google.oauth2 import service_account
from google.auth.transport.requests import Request as _GAuthRequest

from livekit.agents import Agent, AgentServer, AgentSession, cli, room_io
from livekit.plugins import google, noise_cancellation

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
LOCATION = "us-central1"
MODEL = "gemini-live-2.5-flash-native-audio"

# Build the service-account credentials ONCE, up front, and pre-fetch the access
# token. Why: inside the job subprocess the Gemini Live websocket has a hard-coded
# 10s opening-handshake timeout (google-genai SDK). If credential discovery
# (google.auth.default() re-scanning the JSON) and the OAuth token fetch happen on
# the event loop *while* the LiveKit room is also connecting, they eat into that 10s
# budget and the handshake times out (observed). Pre-resolving here removes auth from
# the connect hot path. Passing credentials explicitly also stops the plugin from
# calling google.auth.default() at all.
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
    _CREDS.refresh(_GAuthRequest())  # warm the token off the handshake critical path
except Exception as _e:  # non-fatal; the SDK will refresh on demand
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
    session = AgentSession(
        llm=google.realtime.RealtimeModel(
            model=MODEL,
            vertexai=True,
            project=PROJECT_ID,
            location=LOCATION,
            credentials=_CREDS,   # explicit -> skips google.auth.default() discovery
            voice="Puck",
            temperature=0.8,
        ),
        # CRITICAL: turn_detection=None. If omitted, AgentSession defaults to
        # inference.TurnDetector() (agent_session.py:366), which loads an ML model
        # ON the event loop during startup and blocks it for ~6s ("job executor
        # unresponsive"), which in turn makes the WebRTC media connection time out
        # ("wait_pc_connection timed out") and the whole session crawl. Gemini
        # native-audio does turn detection server-side, so we don't need a local one.
        turn_detection=None,
    )

    # Diagnostic logging: print whenever the agent hears the user or speaks.
    # If you SEE "USER SAID" lines when you talk, your mic IS reaching the agent.
    from livekit.agents import (
        UserInputTranscribedEvent,
        ConversationItemAddedEvent,
    )

    @session.on("user_input_transcribed")
    def _on_user(ev: UserInputTranscribedEvent):
        print(f"\n[USER SAID] {ev.transcript!r} (final={ev.is_final})")

    @session.on("conversation_item_added")
    def _on_item(ev: ConversationItemAddedEvent):
        print(f"\n[{ev.item.role.upper()}] {ev.item.text_content!r}")

    await session.start(
        agent=VoiceAgent(),
        room=ctx.room,
        room_input_options=room_io.RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    # Speak first, so we can verify the audio-OUT path independently of the mic.
    # If you HEAR this greeting, output + Gemini work and only mic input is suspect.
    await session.generate_reply(
        instructions="Greet the user warmly in one short sentence and ask how you can help."
    )


server = AgentServer()


# No agent_name -> automatic dispatch: the worker is offered every new room on the
# project and joins it. Simplest reliable behavior for a single-user demo (no token
# dispatch config, no room-name coordination, no stale-room gotcha). For production /
# multi-agent routing, set agent_name=... here to switch back to explicit dispatch.
@server.rtc_session()
async def _entrypoint(ctx):
    await entrypoint(ctx)


if __name__ == "__main__":
    cli.run_app(server)
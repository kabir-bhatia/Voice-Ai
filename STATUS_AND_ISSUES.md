# Voice Agent v3 — Status & Issues Report

## What We're Building

A real-time voice agent that incrementally replaces Google Gemini API components with self-hosted models running on a GCP VM (NVIDIA L4, 24GB VRAM, Ubuntu 24.04, asia-southeast1). The agent uses LiveKit for WebRTC transport and the LiveKit Agents SDK (Python v1.6.3) for orchestration.

### Incremental Plan (replace one component at a time, test after each):

- **Step 1**: Replace TTS only → Google STT + Google Gemini LLM + self-hosted Kokoro TTS
- **Step 2**: Replace STT too → self-hosted Whisper STT + Google Gemini LLM + self-hosted Kokoro TTS
- **Step 3**: Replace LLM too → self-hosted Whisper STT + self-hosted Qwen LLM + self-hosted Kokoro TTS (fully self-hosted, no Google APIs)

### Working v2 (baseline):
The v2 project (`D:\VoiceAgentProject\v2`, repo: https://github.com/kabir-bhatia/Voice-Ai) works end-to-end using Gemini Live API (`gemini-live-2.5-flash-native-audio`) as a single end-to-end speech-to-speech model. It handles STT + LLM + TTS + turn detection all in one realtime model. This is the "pure realtime" architecture.

---

## Current State: Step 1 — Pipeline Mode (TTS replaced with self-hosted Kokoro)

### Architecture:
```
User (browser) ──WebRTC──> LiveKit Cloud ──> VM agent worker (agent.py)
                                               ├─ Google Cloud STT (streaming, speech-to-text)
                                               ├─ Google Gemini 2.5 Flash LLM (text reasoning)
                                               └─ Self-hosted Kokoro TTS (text-to-speech, on GPU)
```

### VM Details:
- IP: 35.240.243.226
- SSH: `ssh -i "D:\VoiceAgentProject\google_compute_engine" tech_rhobots_ai@35.240.243.226`
- GPU: NVIDIA L4, 24GB VRAM (compute capability 8.9, CUDA 13.0, driver 580.159.03)
- Docker: installed, NVIDIA Container Toolkit configured
- Python: 3.12.3 (uv venv uses Python 3.14.6)
- uv: 0.11.23 installed at ~/.local/bin/uv
- Project files: ~/v3/
- GCP service account key: ~/v3/service-account-key.json
- GCP project: project-0bfd279e-1899-43a5-9de (project number: 93303396272)
- LiveKit Cloud: wss://voice-agent-mt0657cb.livekit.cloud

### What's deployed on the VM:
- `~/v3/agent.py` — pipeline mode agent (Google STT + Google LLM + Kokoro TTS)
- `~/v3/docker-compose.yml` — Kokoro-FastAPI GPU container
- `~/v3/.env.local` — LiveKit + GCP credentials (gitignored)
- `~/v3/pyproject.toml` — dependencies (livekit-agents[google,silero], livekit-plugins-openai, etc.)

### Kokoro TTS:
- Docker image: `ghcr.io/remsky/kokoro-fastapi-gpu:latest`
- Port: 127.0.0.1:8002 → 8880 (OpenAI-compatible /v1/audio/speech endpoint)
- VRAM usage: ~885 MiB
- Voice: af_alloy
- Smoke test confirmed: 200 OK, 140KB WAV for "Hello, this is a test of speech synthesis."
- **Issue**: Container keeps stopping between test sessions (possibly OOM or crash). Needs `restart: unless-stopped` (already set in docker-compose.yml) but it still dies.

---

## Issues Encountered

### Issue 1: Half-cascade is impossible with Vertex AI Gemini models

**What we tried first**: Keep Gemini Live API as the "brain" (audio input comprehension + reasoning + turn detection) but set `modalities=[Modality.TEXT]` so it outputs text, which would route through self-hosted Kokoro TTS. This is the "half-cascade" architecture documented in LiveKit docs.

**Why it failed**: All Gemini Live API models on Vertex AI are "native-audio" models (`gemini-live-2.5-flash-native-audio`). The Google API returns:
- `gemini-2.5-flash is not supported in the live api.` — regular chat models don't support Live API
- `Text output is not supported for native audio output model.` — native-audio models can only output audio, not text

**Conclusion**: Half-cascade (realtime model + separate TTS) is architecturally impossible with the current Vertex AI Gemini model lineup. We had to pivot to full pipeline mode (separate STT + LLM + TTS components).

### Issue 2: Google Cloud Speech-to-Text API not enabled (403 FORBIDDEN)

**Error**: When the agent tries to use `google.STT()` for speech recognition:
```
Cloud Speech-to-Text API has not been used in project 93303396272 before 
or it is disabled. Enable it by visiting:
https://console.developers.google.com/apis/api/speech.googleapis.com/overview?project=93303396272
```

**Root cause**: The GCP project has Vertex AI API enabled (for Gemini Live), but Cloud Speech-to-Text API is a **separate Google Cloud service** that needs to be enabled independently in the GCP console. This is a manual one-click step in the Google Cloud Console.

**Fix options**:
1. Enable the API at: https://console.developers.google.com/apis/api/speech.googleapis.com/overview?project=93303396272
2. OR skip Google STT entirely and use self-hosted Whisper (Step 2) — which we planned to do anyway

### Issue 3: Kokoro TTS failing during live sessions

**Error**: When the agent's `generate_reply()` produced text ("Hey there! How can I help you today?"), the Kokoro TTS failed:
```
failed to synthesize speech: no audio frames were pushed for text: 
"Hey there! How can I help you today?"
```

**Possible causes**:
1. The Kokoro Docker container had already stopped by the time the agent tried to use it (container keeps dying between tests)
2. The `response_format="wav"` parameter might not be supported in streaming mode — Kokoro-FastAPI may need a different format for streaming TTS
3. The OpenAI TTS plugin's streaming behavior may not be compatible with Kokoro-FastAPI's non-streaming endpoint

**Note**: The standalone curl test worked fine (200 OK, 140KB WAV), so Kokoro itself works. The issue is either container stability or streaming compatibility.

### Issue 4: Python version mismatch

The VM's system Python is 3.12.3, but `uv` created a venv with Python 3.14.6 (`cpython-3.14.6-linux-x86_64-gnu`). This shouldn't cause issues but is worth noting — some library compatibility may differ.

### Issue 5: Deprecated API warnings

The LiveKit SDK v1.6.3 emits deprecation warnings:
- `turn_detection is deprecated and will be removed in v2.0. Use turn_handling=TurnHandlingOptions(...) instead`
- `RoomInputOptions and RoomOutputOptions are deprecated, use RoomOptions instead`

These are warnings, not errors, but will need migration before v2.0.

---

## Current agent.py (pipeline mode — deployed on VM)

```python
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
TTS_MODEL = os.environ.get("TTS_MODEL", "kokoro")
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
            response_format="wav",
        ),
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
```

---

## docker-compose.yml (deployed on VM)

```yaml
services:
  kokoro:
    image: ghcr.io/remsky/kokoro-fastapi-gpu:latest
    ports:
      - "127.0.0.1:8002:8880"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    restart: unless-stopped
```

---

## LiveKit SDK Architecture Notes (from research)

### Key findings:
1. **`AgentSession` constructor**: accepts `stt=`, `llm=`, `tts=`, `turn_detection=` (deprecated → `turn_handling=TurnHandlingOptions(...)` in v2.0), `vad=`, `noise_cancellation` (on room_io, not session)
2. **Pipeline mode**: pass `stt=STT(...)`, `llm=LLM(...)` (not RealtimeModel), `tts=TTS(...)`. Turn detection defaults to `inference.TurnDetector()` (silero VAD + model-based).
3. **Realtime mode**: pass `llm=RealtimeModel(...)`. No separate stt/tts needed. Turn detection handled server-side.
4. **Half-cascade**: `llm=RealtimeModel(modalities=[TEXT])` + `tts=TTS(...)`. **Only works with non-native-audio models** — but all Vertex AI Live API models ARE native-audio, so this is impossible on Vertex AI.
5. **Self-hosted STT**: use `openai.STT(base_url=...)` with `StreamAdapter` + silero VAD (Whisper doesn't natively stream)
6. **Self-hosted TTS**: use `openai.TTS(base_url=...)` pointing at Kokoro-FastAPI
7. **Self-hosted LLM**: use `openai.LLM(base_url=...)` pointing at vLLM

### Valid Vertex AI Live API models (from plugin source code):
- `gemini-live-2.5-flash-native-audio` (GA, Vertex AI)
- `gemini-2.5-flash-native-audio-preview-12-2025` (Google AI API)
- `gemini-3.1-flash-live-preview` (preview, has compatibility limitations — breaks `generate_reply()`)

### Google plugin pipeline classes:
- `google.STT(languages=, model=, location=, credentials_file=)` — Cloud Speech-to-Text
- `google.LLM(model=, vertexai=True, project=, location=, credentials=, temperature=)` — Gemini text model
- `google.TTS(...)` — also available but not used (we use Kokoro instead)

---

## All processes currently STOPPED on VM:
- Agent worker: killed (tmux session `agent` terminated)
- Kokoro container: stopped (`docker compose down`)
- GPU: 0 MiB used
- No API usage occurring

## To resume work:
1. SSH: `ssh -i "D:\VoiceAgentProject\google_compute_engine" tech_rhobots_ai@35.240.243.226`
2. Start Kokoro: `cd ~/v3 && docker compose up -d`
3. Start agent: `tmux new -d -s agent 'cd ~/v3 && export PATH=/home/tech_rhobots_ai/.local/bin:/usr/bin:/bin:/usr/local/bin:$PATH && uv run python agent.py dev > /tmp/agent.log 2>&1'`
4. Check logs: `cat /tmp/agent.log`
5. Generate test token (on laptop): `cd D:\VoiceAgentProject\v2 && uv run python generate_token.py --room test --minutes 10`

---

# Session Log — 2026-06-23 (Step 1 made fully working + latency tuned)

## TL;DR
Step 1 (Google STT + Gemini LLM + self-hosted Kokoro TTS) now **works end-to-end** in a
real browser conversation. Both prior blockers fixed, latency measured live, and the #1
latency fix (endpointing) applied in code.

## 1. Google STT 403 — RESOLVED
- The Cloud Speech-to-Text API was enabled in the GCP console by the user.
- Verified the **same service account** can now use it: transcribed Google's public sample
  clip → `"how old is the Brooklyn Bridge"` at **0.93 confidence**. No 403, no auth changes
  needed. (Test ran from `~/v3` venv against `gs://cloud-samples-data/speech/brooklyn_bridge.flac`.)

## 2. Kokoro TTS "no audio frames" — ROOT CAUSE FOUND + FIXED
Two things, both in how LiveKit's `openai.TTS` plugin talks to Kokoro-FastAPI:
1. **Model name routing (the real cause).** `tts.py` only sends raw audio (`iter_bytes`) for
   model names in `AUDIO_STREAM_MODELS = {"tts-1","tts-1-hd"}`. **Any other name (incl.
   `"kokoro"`) takes the SSE path**, which parses for `data:` events Kokoro never emits →
   zero frames → "no audio frames were pushed". Fix: set **`TTS_MODEL=tts-1`** (Kokoro
   ignores the model field, so this is safe).
2. **Response format.** `response_format="wav"` doesn't stream-decode (WAV header); the
   plugin frames bytes as `audio/<fmt>` at `SAMPLE_RATE=24000`. Fix: **`response_format="pcm"`**
   (raw 24 kHz = Kokoro's native rate).
- Verified in isolation (no LiveKit room, no Vertex billing): **25 frames, 24 kHz, 2.05 s
  audio — "TTS PCM TEST OK"**.
- Applied in `agent.py` (defaults `tts-1` + `pcm`, with explanatory comments), `.env.example`,
  and the VM's `.env.local`.

## 3. End-to-end live test — SUCCESS
Browser → LiveKit Cloud → VM worker (Google STT → Gemini 2.5 Flash → Kokoro TTS). Held a
real spoken conversation (e.g. "Hi, how are you?" → "Hey there! I'm doing great…"). The
"no audio frames" error is gone; only harmless warnings remain (`no request_id`, deprecation).

## 4. Latency — measured live (4 real turns)
End-to-end (you stop talking → agent starts talking): **~2.0–3.2 s**. Breakdown:
- **End-of-turn detection wait: 0.85 s (confident) … 2.5 s (unsure) — DOMINANT.**
- Google STT transcript delay: ~0.5–0.8 s (cloud round-trip).
- LLM first token + TTS call: ~0.02–0.7 s (`preemptive_generation` already on, generating
  during the endpointing wait).
- Kokoro TTS first audio: **~0.15 s** (measured separately; 7–14× realtime). Not a bottleneck.
Takeaway: the models are fast; the wait is almost entirely turn-detection endpointing.

## 5. Latency fix — Lever 1 applied (endpointing) — LOCAL ONLY, not yet on VM
- `agent.py` `AgentSession(... turn_handling={"endpointing": {"min_delay": 0.3, "max_delay": 1.2}})`.
- Caps the unsure-turn wait from default **2.5 s → 1.2 s**; expected slow turns ~2.7–3.2 s →
  **~1.2–1.7 s**. `preemptive_generation` stays on (other turn_handling keys keep defaults).
- **NOT yet on the VM** — next session: `scp agent.py` to `~/v3/agent.py`, then test by ear
  (raise `max_delay` if it interrupts; lower if still laggy).
- Levers 2 & 3 (deferred): self-hosted Whisper STT removes ~0.5–0.8 s; self-hosted Qwen
  (vLLM) removes the Gemini cloud hop. These are Steps 2 & 3.

## 6. VM / infra notes
- Confirmed: **NVIDIA L4 24 GB, Ubuntu 24.04, asia-southeast1 (Singapore)**, driver 580.
- Docker + NVIDIA Container Toolkit + uv are installed on the VM (kept).
- SSH key on laptop needed a perms fix for Windows OpenSSH:
  `icacls "D:\VoiceAgentProject\google_compute_engine" /inheritance:r /grant:r "$($env:USERNAME):R"`.
- Ephemeral external IP changes on VM restart — re-check the IP if SSH times out.

## 7. Cost discipline
- Every test session billed Vertex (STT + Gemini) only while running; kept short.
- After each test: `tmux kill-server` + `docker compose down`; verified **GPU 0 MiB**, no
  processes. VM itself bills while powered on (manager-owned; power-off is on their side).

## Files changed today (local repo)
- `agent.py` — `TTS_MODEL` default `tts-1`, `response_format="pcm"`, `turn_handling` endpointing.
- `.env.example` — `TTS_MODEL=tts-1` (with note).
- VM `~/v3/.env.local` — `TTS_MODEL` set to `tts-1` (agent.py already scp'd with pcm; the
  endpointing change still needs to be scp'd next session).

## Next session checklist
1. `scp agent.py` to `~/v3/agent.py` (gets the endpointing fix onto the VM).
2. Live test, measure latency by ear; tune `max_delay`.
3. Step 2: self-hosted Whisper STT. Step 3: self-hosted Qwen (vLLM).

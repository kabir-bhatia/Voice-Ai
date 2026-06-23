# Voice Agent v2 — Project Notes & Current Status

_Last updated: 2026-06-21_

---

## 1. Project Overview

A real-time voice agent using **LiveKit Agents SDK** + **Google Gemini Live API** (Vertex AI). You speak into a mic, Gemini speaks back — with barge-in/interruption support, noise cancellation, and turn detection handled automatically by LiveKit.

### Two Versions

| | v1 (`real_time_voice_agent2.py`) | v2 (`v2/agent.py`) |
|---|---|---|
| Framework | Raw `google-genai` SDK + PyAudio | LiveKit Agents SDK |
| Transport | Direct WebSocket to Gemini | WebRTC via LiveKit Cloud |
| Audio I/O | Manual PyAudio mic/speaker | LiveKit handles all audio |
| Turn detection | Manual (`CLEAR` signal barge-in hack) | Built-in (`turn-detector-v1`) |
| Noise cancellation | None | Built-in (BVC plugin) |
| Auth | Manual ADC credential loading | Service account key (no `gcloud` hang) |
| Client | Python terminal only | Browser, terminal, mobile |
| Multi-user | No | Yes (rooms model) |
| Production ready | No (local demo) | Yes (LiveKit Cloud or self-hosted) |
| Billing | Vertex AI $300 GCP credits | Same Vertex AI billing |

---

## 2. Architecture

```
User (browser/terminal)
    |
    | WebRTC audio/video
    v
LiveKit Cloud (wss://<your-project>.livekit.cloud)
    |
    | Room dispatch
    v
Agent Worker (agent.py on your laptop)
    |
    | Gemini Live API (RealtimeModel, speech-to-speech)
    v
Vertex AI (us-central1)  ←  bills $300 GCP credits
```

---

## 3. Files in `v2/`

| File | Purpose |
|---|---|
| `agent.py` | Main agent — LiveKit AgentServer + Gemini Live RealtimeModel (Vertex AI) |
| `generate_token.py` | Generates LiveKit join tokens with auto-dispatch (for browser testing) |
| `start_console.bat` | Convenience script to start the agent with all env vars set |
| `.env.local` | Credentials (LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, GOOGLE_APPLICATION_CREDENTIALS, GOOGLE_CLOUD_PROJECT) |
| `.env.example` | Template (no secrets) |
| `.gitignore` | Excludes `.env.local`, `*.json` (service account keys), `__pycache__/`, `.venv/` |
| `pyproject.toml` | Dependencies managed by `uv` |
| `service-account-key.json` | GCP service account key (Vertex AI auth) |
| `service_account_details.txt` | Service account email reference |

### Dependencies (installed via `uv`)

- `livekit-agents[google,silero]~=1.6` — Core framework + Gemini plugin + VAD
- `livekit-plugins-noise-cancellation~=0.2` — BVC noise cancellation
- `livekit-api>=1.1.0` — Token generation (for browser testing)
- `python-dotenv` — Loads `.env.local`

---

## 4. Environment

| Item | Value |
|---|---|
| OS | Windows 11 |
| Python | 3.12 (via `uv`, in `.venv`) — avoids Python 3.13 WMI hang |
| LiveKit SDK | `livekit-agents` 1.6.2 |
| Google Plugin | `livekit-plugins-google` 1.6.2 |
| LiveKit Cloud | `wss://<your-project>.livekit.cloud` |
| GCP Project | `<your-gcp-project-id>` |
| GCP Billing | $300 credits linked |
| Vertex AI Model | `gemini-live-2.5-flash-native-audio` on `us-central1` |
| LiveKit CLI | v2.16.6 (installed via `winget`) |

---

## 5. Authentication Setup

### Why Service Account (not ADC or API key)

| Method | Problem |
|---|---|
| `GOOGLE_API_KEY` (Developer API) | Another project already has this env var set globally. LiveKit's Google plugin reads it first and ignores `GOOGLE_APPLICATION_CREDENTIALS`. Also, Developer API doesn't bill the $300 GCP credits. |
| ADC (`application_default_credentials.json`) | Requires `google.auth.default()` which shells out to `gcloud` — which hangs on this machine (same bug as v1). Needs workarounds. |
| **Service Account key** (chosen) | `google.auth.default()` reads the JSON file directly — no `gcloud` subprocess. No env var conflict. Officially supported by LiveKit. Bills the $300 credits. |

### Service Account Details

- **Email**: `<service-account-email — see service_account_details.txt (gitignored)>`
- **Roles**: Agent Platform User (`roles/aiplatform.user`) + Vertex AI Service Agent (`roles/aiplatform.serviceAgent`)
- **File**: `service-account-key.json` (in `v2/`, gitignored)
- **Key type**: `type: service_account` (has `private_key`, not `refresh_token`)

### The `GOOGLE_API_KEY` Conflict

The user's system has `GOOGLE_API_KEY` set globally for another project. This causes LiveKit's Google plugin to use the Developer API path instead of Vertex AI. **Solution**: `os.environ.pop("GOOGLE_API_KEY", None)` at the top of `agent.py`, before any LiveKit/Google imports.

---

## 6. What Works

### ✅ Console mode (`lk agent console agent.py`)

Tested and fully working on 2026-06-21:
- Agent connects to Vertex AI via service account
- Gemini Live API responds with voice
- Speech recognition works (user speech was transcribed)
- Agent responds (though it was in translation mode due to a Gemini default system prompt overriding our instructions — see Known Issues)
- WMI fix applied (though `uv` uses Python 3.12, so it's not needed, but kept as safety net)
- No `gcloud` subprocess hang
- No `GOOGLE_API_KEY` conflict
- Service account auth verified: `Project: <your-gcp-project-id>`, `Cred type: Credentials`

### ✅ Dev mode (`python agent.py dev`)

- Worker registers with LiveKit Cloud successfully
- No errors in startup

### ✅ Token generation (`generate_token.py`)

- Generates JWT tokens with `roomConfig.agents` for auto-dispatch
- Verified token contains `"roomConfig": {"agents": [{"agentName": "voice-agent"}]}`

---

## 7. Browser Testing — ✅ FULLY WORKING END-TO-END (2026-06-21)

**Status: browser voice chat works.** User joins via `meet.livekit.io`, the agent
joins, greets, and responds to speech in real time with barge-in. Three stacked bugs
had to be fixed to get here (dispatch → handshake timeout → event-loop block); each is
documented below.

### Root cause (confirmed from installed `livekit-agents` 1.6.2 source)

`agent.py` registered the worker with an **explicit agent name**
(`@server.rtc_session(agent_name="voice-agent")`). Per `worker.py:218`, an explicit
agent name enables **explicit dispatch** — *"jobs will not be dispatched to rooms
automatically."* The worker then only joins a room if the server explicitly dispatches
it (via the token's `roomConfig.agents` or the dispatch API). Although
`generate_token.py` did embed that dispatch config, explicit dispatch is fragile: the
token's room config is only applied when the room is **first created**, so a stale
`voice-test` room (left over from earlier `lk dispatch` tests) silently ignored it.

Note: `console` mode worked because it runs a **local mock room** (`simulate_job`,
`cli/cli.py:228`) and never exercises dispatch at all. `dev` connects to LiveKit Cloud
as a real worker — the correct mode for browser testing.

### Fix applied — switch to automatic dispatch

- `agent.py`: `@server.rtc_session()` with **no** `agent_name` → automatic dispatch.
  The worker is offered every new room on the project and joins it. Simplest reliable
  behavior for a single-user demo.
- `generate_token.py`: removed the `roomConfig.agents` block (no longer needed) and
  the default `--room` is now a fresh unique `demo-XXXXXX` name (no stale-room collision).
- Deliberate **demo-vs-production tradeoff**: explicit dispatch is correct in
  production (don't attach an agent to every room); reintroduce it for Plan B if
  multi-agent routing is ever needed.

Diagnostic for future: a real dispatch prints `"received job request"` in the `dev`
terminal (`worker.py:1403`). No such line on browser join = worker never dispatched.

### Follow-on problem after dispatch fix — Gemini Live handshake timeout — ✅ FIXED

Once dispatch worked, the agent joined the room but then failed with:
`Gemini Realtime API error: timed out during opening handshake` and the session closed.

**Not a config error.** A direct probe from a clean process connected to the *exact*
same model/project/region in **2.5s** (`gemini-live-2.5-flash-native-audio`,
us-central1, service-account auth). Root cause: the genai SDK's Live websocket has a
**hard-coded 10s opening-handshake timeout**. Inside the job subprocess, credential
discovery (`google.auth.default()` re-scanning the JSON) + the OAuth token fetch +
the LiveKit room connection all run on the event loop at once (note the
"room connection not established within 10s" warning), so the handshake overran 10s.

**Fix (`agent.py`):**
- Build the service-account credentials once at import and **pre-refresh the token**
  (off the handshake critical path), then pass `credentials=_CREDS` to `RealtimeModel`
  so the plugin never calls `google.auth.default()`.
- Removed `turn_detection="turn-detector-v1"` — ignored for a RealtimeModel (Gemini
  does server-side turn detection); it only added a startup warning/load.

Note: the half-cascade model `gemini-live-2.5-flash` is **not** enabled on this project
(probe returned 1008). We don't need it — the native-audio model is the one in use.

### Third problem — agent joined but unresponsive / extremely sluggish — ✅ FIXED

After the handshake fix the agent joined but didn't react to voice; logs showed
`job executor is unresponsive {"delay": 5914}` and `wait_pc_connection timed out`, and
the mic stream only attached ~7 minutes late.

**Root cause:** `AgentSession` defaults `turn_detection` to `inference.TurnDetector()`
(`agent_session.py:366`) — a local ML model that **loads on the event loop during
startup and blocks it ~6s**. That frozen loop made the WebRTC media connection time out
and cascaded into the whole session crawling.

**Fix (`agent.py`):** pass `turn_detection=None` to `AgentSession`. Gemini native-audio
does turn detection server-side, so no local detector is needed. After this the agent
connects in seconds, greets immediately, and responds in real time.

Also added (diagnostics, kept in `agent.py`): a startup `generate_reply` greeting and
`user_input_transcribed` / `conversation_item_added` event logging that print
`[USER SAID] ...` / `[ASSISTANT] ...` to the terminal.

### Original symptoms (for reference)

1. `python agent.py dev` starts successfully, worker registers with LiveKit Cloud
2. Open `meet.livekit.io` with Custom server URL + token
3. Browser joins the room — user appears as participant
4. **Agent worker never joins the room** — no audio, no response

### What Was Tried

| Approach | Result |
|---|---|
| `meet.livekit.io` with manual token (no dispatch) | User joins room, agent doesn't |
| `lk dispatch create --agent-name voice-agent --room voice-test` | Dispatch created, but agent was offline at that point |
| Token with `roomConfig.agents` (auto-dispatch) | Generated correctly, but agent still doesn't join when user connects via browser |

### Root Cause Analysis

The `lk agent dev` command registers the worker, but the **dispatch mechanism** isn't triggering the agent to join the room. Possible causes:

1. **Agent name mismatch**: The `rtc_session` decorator sets `agent_name="voice-agent"`, and the token dispatch uses `agentName: "voice-agent"`. These should match. Need to verify what name the worker actually registers with.

2. **Worker registration vs dispatch timing**: The worker registers via `dev` mode, but when a user creates a room via the token, the dispatch might not reach the worker. `dev` mode may not support incoming dispatches — it might only work with `lk agent console` (which creates a mock room).

3. **`dev` mode dispatch handling**: In `dev` mode, LiveKit Agents may not listen for dispatch requests. The `lk agent dev` docs say it "starts a worker against your LiveKit server with hot-reloading and readable logs" — but it may not handle room dispatches in the same way as `start` (production mode).

4. **Network/firewall**: The worker registers over WebSocket to `wss://<your-project>.livekit.cloud`, but the dispatch response may not reach back to the local machine (though this is unlikely since the registration succeeded).

### What Needs to Be Debugged

1. **Check if the worker receives the dispatch**: When the user connects in the browser, check the terminal running `agent.py dev` for any new log lines. If no log lines appear, the worker isn't receiving the dispatch.

2. **Try `lk agent start` (production mode)** instead of `dev` — this runs the worker in production mode which should handle dispatches properly.

3. **Try the LiveKit Cloud dashboard**: Go to https://cloud.livekit.io → your project → Agents tab. Check if the worker appears as registered. Check the Rooms tab when the user connects.

4. **Try `lk agent console` for now**: This already works and bypasses the dispatch issue entirely. For local testing, this is the simplest path.

---

## 8. Known Issues

### Issue: Agent responds in "translation mode" instead of custom instructions

When tested via `lk agent console`, the Gemini Live model ignored our custom `instructions` parameter and responded with translation-style answers (e.g., "What is your name? is What is your name? in English"). 

**Cause**: The `lk agent console` mock room may not pass the agent's instructions correctly through to the RealtimeModel, or Gemini Live's native audio mode may override system instructions with its own default prompt.

**Potential fix**: Test with browser mode (once dispatch works) or try passing instructions differently (as the `instructions` parameter of `RealtimeModel` instead of the `Agent` class).

### Issue: Unicode encoding error in console transcript

```
UnicodeEncodeError: 'charmap' codec can't encode characters in position 95-97
```

This happens when Gemini responds with non-ASCII characters (e.g., Hindi text from the transcription) and the Windows console can't encode them. Non-blocking — the agent still works, just the transcript display in console mode has encoding issues.

---

## 9. Cost Safety

- **Vertex AI Live API bills per second of audio** while connected — not free
- **Always end sessions with Ctrl+C** — billing stops when the process ends
- **Kill orphan processes after testing**: `Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force`
- **LiveKit Cloud free tier** — no charges for the agent framework itself
- **Short test sessions only** — keep tests under 60 seconds initially

---

## 10. Quick Reference Commands

```powershell
# === Start the agent (console mode — WORKS) ===
cd D:\VoiceAgentProject\v2
uv run python agent.py dev
# Then: lk agent console agent.py (in another terminal)

# === Or use the convenience bat file ===
.\start_console.bat

# === Kill all Python processes (cost safety) ===
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force

# === Generate a browser join token (60 min TTL) ===
cd D:\VoiceAgentProject\v2
uv run python generate_token.py --room voice-test --minutes 60

# === Dispatch agent to a room manually ===
# LiveKit creds live in .env.local (gitignored). Load them into this shell first:
#   Get-Content .env.local | ForEach-Object { if ($_ -match '^\s*(\w+)=(.*)$') { Set-Item -Path "env:$($Matches[1])" -Value $Matches[2] } }
& "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\LiveKit.LiveKitCLI_Microsoft.Winget.Source_8wekyb3d8bbwe\lk.exe" dispatch create --api-key $env:LIVEKIT_API_KEY --api-secret $env:LIVEKIT_API_SECRET --url $env:LIVEKIT_URL --agent-name voice-agent --room voice-test

# === Verify service account auth (no billing) ===
cd D:\VoiceAgentProject\v2
$env:GOOGLE_APPLICATION_CREDENTIALS="D:\VoiceAgentProject\v2\service-account-key.json"
$env:GOOGLE_CLOUD_PROJECT="<your-gcp-project-id>"
.venv\Scripts\python.exe -c "from google.auth import default; creds, project = default(); print(f'Project: {project}'); print(f'Cred type: {type(creds).__name__}')"
```

---

## 11. Next Steps (Priority Order)

1. ~~**Fix browser dispatch issue**~~ — ✅ DONE (see §7). Automatic dispatch.
2. ~~**Gemini handshake timeout**~~ — ✅ DONE (see §7). Pre-built/pre-refreshed SA creds.
3. ~~**Agent unresponsive / sluggish**~~ — ✅ DONE (see §7). `turn_detection=None`.
4. ~~**Test browser mode end-to-end**~~ — ✅ DONE. Speak in → Gemini voice out, real time.

### Remaining / open

- **Confirm custom instructions in browser** (was the §8 "translation mode" issue, only
  ever seen in the console mock). On the working browser run, verify the agent follows
  its persona (concise, conversational). If it drifts, move the system prompt onto
  `RealtimeModel(instructions=...)` instead of the `Agent` class.
- **Productionize `agent.py`** — gate the diagnostic greeting + `[USER SAID]` logging
  behind an env flag (e.g. `AGENT_DEBUG=1`) so normal runs are clean.
- **Self-hosted frontend** (optional) — replace `meet.livekit.io` with a minimal local
  web page using the LiveKit JS SDK, so testing isn't tied to a third-party UI.
- **Plan B: self-hosted GPU pipeline** — run LiveKit server + agent on the GPU server
  over SSH, swapping Gemini Live for open-source STT → LLM → TTS. See §7 of the root
  `PROJECT_NOTES.md` for the target architecture and SSH considerations.

---

## 12. Credential Reference

| Credential | Value | Stored In |
|---|---|---|
| LiveKit URL | `wss://<your-project>.livekit.cloud` | `.env.local` |
| LiveKit API Key | `<redacted — see .env.local>` | `.env.local` |
| LiveKit API Secret | `<redacted — see .env.local>` | `.env.local` |
| GCP Project ID | `<your-gcp-project-id>` | `.env.local`, service account key |
| Service Account Key | `service-account-key.json` | File in `v2/` directory |
| Service Account Email | `<service-account-email — see service_account_details.txt (gitignored)>` | `service_account_details.txt` |
| Vertex AI Model | `gemini-live-2.5-flash-native-audio` | `agent.py` |
| Vertex AI Location | `us-central1` | `agent.py` |
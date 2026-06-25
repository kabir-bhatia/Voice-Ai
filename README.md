# Fully Self-Hosted Realtime Voice Agent (v5)

A real-time, **100% self-hosted** voice agent — you talk, it talks back — with **no cloud
AI APIs** (no OpenAI, no Google). Speech-to-text, the LLM, and text-to-speech all run
locally on your own GPU. [LiveKit](https://livekit.io) is used only for the WebRTC
transport (browser ↔ agent).

```
Browser (meet.livekit.io)
   ⇅  WebRTC
LiveKit Cloud  (free tier — transport only)
   ⇅
Agent worker  (agent.py, this repo)
   ├─ vLLM            :8000   Qwen2.5-7B-Instruct (AWQ)      ← the "brain" (LLM)
   ├─ faster-whisper  :8001   large-v3-turbo                 ← speech-to-text (STT)
   └─ Kokoro-FastAPI  :8002   af_heart                       ← text-to-speech (TTS)
   (silero VAD + turn detection run inside the worker)
```

All three model servers are OpenAI-compatible and bound to `localhost`; the agent talks
to them over `127.0.0.1`.

---

## Requirements (read this first)

This is **not** a laptop app — it needs a real GPU box:

- **Linux** host with an **NVIDIA GPU, ~16 GB+ VRAM** (developed on an NVIDIA L4 24 GB).
  Budget: vLLM ~12 GB + Whisper ~3 GB + Kokoro ~1.5 GB.
- **NVIDIA driver** + **Docker** + **NVIDIA Container Toolkit** (so containers see the GPU).
- **[uv](https://docs.astral.sh/uv/)** (Python package manager) for the agent worker.
- A **free [LiveKit Cloud](https://cloud.livekit.io) account** (for the WebRTC transport) —
  you'll need its `URL`, `API key`, and `API secret`.
- First run downloads **~15 GB** of model weights/images (cached afterward).

> No NVIDIA GPU = it won't run. The model servers are GPU-only.

---

## 1. One-time host setup

```bash
# Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker

# NVIDIA Container Toolkit (lets containers use the GPU)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker

# Verify the GPU is visible to Docker:
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi

# uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 2. Configure

```bash
cp .env.example .env.local
# edit .env.local and fill in your LiveKit Cloud credentials:
#   LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
# (the model endpoints/models are already set for the localhost servers)
```

## 3. Start the model servers (Docker)

```bash
docker compose up -d                 # starts vLLM + whisper + kokoro
docker compose logs -f vllm          # first run downloads ~6GB LLM weights; wait for
                                     # "Application startup complete"
# sanity-check all three are up:
curl -s http://localhost:8000/v1/models      # vLLM
curl -s http://localhost:8001/v1/models      # whisper
curl -s http://localhost:8002/v1/audio/voices # kokoro
```

## 4. Run the agent worker

```bash
uv sync                              # create the venv + install deps
uv run python agent.py dev           # connects to LiveKit Cloud, waits for a room
# leave this running (use tmux/screen on a remote box so it survives disconnects)
```

## 5. Talk to it

```bash
# in another shell (or your laptop), mint a join token:
uv run python generate_token.py --room test --minutes 15
# open the printed meet.livekit.io URL in a browser, allow the mic, and talk.
```

## 6. Shut down

```bash
docker compose down                  # stop the model servers (frees the GPU)
# (Ctrl+C the agent worker)
```

---

## Configuration (`.env.local`)

| Var | Purpose |
|-----|---------|
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | LiveKit Cloud (transport) |
| `LLM_BASE_URL` / `LLM_MODEL` | local vLLM endpoint + model name |
| `STT_BASE_URL` / `STT_MODEL` | local Whisper endpoint + model |
| `TTS_BASE_URL` / `TTS_MODEL` / `TTS_VOICE` | local Kokoro endpoint, `tts-1`, voice |

**Swapping models** (edit `docker-compose.yml` + `.env.local`, keep them in sync):
- **LLM:** `Qwen/Qwen2.5-7B-Instruct-AWQ` → e.g. `Qwen2.5-14B-Instruct-AWQ` for a smarter
  brain (needs more VRAM).
- **STT:** `deepdml/faster-whisper-large-v3-turbo-ct2` (default) ↔
  `Systran/faster-distil-whisper-large-v3` (fastest) / `Systran/faster-whisper-large-v3`
  (max accuracy).
- **TTS voice:** `af_heart` (default), `af_bella`, `am_michael`, … (Kokoro voices). Keep
  `TTS_MODEL=tts-1` — the LiveKit OpenAI TTS plugin only streams raw audio for `tts-1`/`tts-1-hd`.

## Notes / gotchas
- **`TTS_MODEL` must be `tts-1`** (not `kokoro`) and **`response_format=pcm`** in `agent.py` —
  other values make the TTS plugin take an SSE path Kokoro can't satisfy ("no audio frames").
- Models load in a **`prewarm`** step (silero VAD) so they don't block the event loop.
- The agent has **no internet/tools** — it can't fetch live data (weather, etc.) and will
  *hallucinate* such facts. Add function/tool calling for real data.
- Use a recent Python via `uv` (3.12 recommended). The agent itself is CPU-light; the GPU
  work is in the Docker model servers.

## License
See repository.

# Architecture — Fully Self-Hosted Voice Agent (v5)

A real-time voice agent where **every AI component is self-hosted** on a single GPU.
No OpenAI, no Google. LiveKit is used only to move audio between the browser and the agent.

## What hosts the LLM: **vLLM** (not Ollama)

The language model is served by **vLLM** — the official `vllm/vllm-openai` Docker image —
running **Qwen2.5-7B-Instruct (AWQ 4-bit)**.

| | **vLLM** (used here) | Ollama |
|---|---|---|
| Designed for | Production GPU serving — high throughput, low latency (PagedAttention, continuous batching) | Easy local/dev, single-user |
| API | OpenAI-compatible (`/v1/chat/completions`) | OpenAI-compatible (simpler) |
| Models | HF models + AWQ/GPTQ quantization | GGUF models |
| Best for | Real-time agents, concurrency, scale | Quick desktop experiments |

We chose vLLM for production-grade performance and its drop-in OpenAI-compatible endpoint.
**vLLM hosts only the LLM** — STT and TTS run on their own small servers (not vLLM/Ollama).

## Diagram

```
   You (browser — meet.livekit.io)
        ⇅  WebRTC audio
   LiveKit Cloud        ← transport only (free tier); no AI runs here
        ⇅  (agent dials out)
 ┌──────────────────── GCP VM · NVIDIA L4 (24 GB) ────────────────────┐
 │  Agent worker  (agent.py, LiveKit Agents SDK) — the orchestrator    │
 │                                                                     │
 │    1. silero VAD ........ detect speech start/stop  (in-process)    │
 │    2. turn detection .... decide the user is done   (in-process)    │
 │             │  your speech (audio)                                  │
 │             ▼                                                       │
 │    3. STT  →  faster-whisper server   (Docker · :8001)  audio→text  │
 │             │  text                                                 │
 │             ▼                                                       │
 │    4. LLM  →  vLLM + Qwen2.5-7B-AWQ    (Docker · :8000)  text→reply  │
 │             │  reply text                                           │
 │             ▼                                                       │
 │    5. TTS  →  Kokoro-FastAPI           (Docker · :8002)  text→audio  │
 │             │  reply audio                                          │
 │             ▼  back through LiveKit → your browser speaker          │
 └─────────────────────────────────────────────────────────────────────┘
   All STT/LLM/TTS calls are localhost (OpenAI-compatible). Nothing leaves the VM.
```

## Components

| # | Stage | What runs it | Where | Model |
|---|-------|--------------|-------|-------|
| — | Transport | **LiveKit Cloud** (WebRTC) | Cloud (free) | — |
| — | Orchestrator | **Agent worker** (LiveKit Agents SDK) | VM | — |
| 1–2 | VAD + turn detection | **silero** (in-process) | VM (in worker) | silero-vad |
| 3 | **STT** (speech→text) | **faster-whisper-server** (Docker) | VM `:8001` | `large-v3-turbo` |
| 4 | **LLM** (the brain) | **vLLM** (Docker) | VM `:8000` | `Qwen2.5-7B-Instruct-AWQ` |
| 5 | **TTS** (text→speech) | **Kokoro-FastAPI** (Docker) | VM `:8002` | Kokoro, voice `af_heart` |

## Data flow (one turn)

1. You speak in the browser → audio streams to the agent via **LiveKit (WebRTC)**.
2. **silero VAD** detects speech; **turn detection** decides when you've finished.
3. The utterance goes to **faster-whisper** → **text**.
4. The text goes to **vLLM / Qwen2.5-7B** → **reply text** (streamed token-by-token).
5. The reply text goes to **Kokoro** → **audio**, streamed back through LiveKit to your speaker.

Everything in steps 3–5 is a **localhost** call on the VM, so no audio or text leaves the box.

## Why this design

- **Fully self-hosted / private:** no third-party AI APIs; zero per-token cost.
- **Cascade (STT→LLM→TTS):** lets us pick the best model for each stage and swap any one
  independently. In 2026 this is the practical architecture for a *self-hosted realtime*
  agent (native speech-to-speech models that are both smart and self-hostable for realtime
  don't exist yet).
- **OpenAI-compatible everywhere:** the worker talks to all three servers with one client
  pattern; swapping a model is a config change.

## Hosting facts

- One **GCP VM with an NVIDIA L4 (24 GB)** GPU.
- **3 Docker containers** (vLLM + Whisper + Kokoro) share the GPU (~17 GB used).
- The agent worker connects **outbound** to LiveKit Cloud — only SSH is exposed on the VM.

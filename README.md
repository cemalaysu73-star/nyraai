# Nyra v2 — AI Voice Assistant

Nyra is a local-first AI voice assistant for Windows. It listens for a wake word, transcribes your voice, routes commands to the right action or AI model, and responds with natural speech — all running on your own machine.

## Features

- **Wake word detection** — say "Hey Nyra" or "Nyra" to activate
- **Voice commands** — open apps, control volume/media, lock screen, sleep, and more
- **AI conversation** — powered by Groq (free cloud) or Ollama (fully offline)
- **Bilingual** — English and Turkish support
- **Text-to-speech** — edge-tts (online, natural voices) with pyttsx3 fallback
- **Web search** — DuckDuckGo search with AI-synthesized answers
- **Screen awareness** — sees your active window for context-aware replies
- **System control** — brightness, clipboard, file search, hotkeys, process list
- **Night agent** — runs background research tasks while you sleep
- **Memory** — remembers facts and preferences across sessions

## Requirements

- Windows 10/11
- Python 3.11+
- A microphone
- One of:
  - **Groq API key** (free) — [console.groq.com](https://console.groq.com)
  - **Ollama** (offline) — [ollama.com](https://ollama.com)

## Quick Start

1. Clone or download this repo
2. Double-click **SETUP.bat** — installs everything automatically
3. Open `data/secrets.env` and add your Groq key:
   ```
   GROQ_API_KEY=your_key_here
   ```
4. Double-click **Nyra.bat** to launch

## Manual Setup

```bash
python -m venv .venv
.venv\Scripts\pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pythonw main.py
```

## Voice Commands

| Command | Action |
|---|---|
| `open chrome / steam / spotify` | Launch apps |
| `volume up / down / mute` | Audio control |
| `next track / pause music` | Media control |
| `lock screen` | Lock Windows |
| `take a screenshot` | Save to Pictures |
| `what's in my clipboard` | Read clipboard |
| `system info` | CPU / RAM / disk stats |
| `search for X` | Web search + AI answer |
| `google X` | Open browser search |
| `open downloads / desktop` | Open folders |
| `find file X` | Search home directory |

## Configuration

Edit `config.py` to change:
- `whisper_model_size` — `"tiny"` (fastest) to `"large"` (most accurate)
- `llm_provider` — `"groq"` or `"ollama"`
- `ollama_model` — any model you have pulled
- `en_voice` / `tr_voice` — edge-tts voice names
- `wake_phrases` — customize the wake word

## Offline Mode

Install [Ollama](https://ollama.com) and pull a model:
```bash
ollama pull qwen2.5:7b
```
Set `llm_provider = "ollama"` in `config.py`. Nyra will work fully offline.

## Building an Executable

```bash
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller nyra.spec --clean -y
```
Output: `dist/Nyra/Nyra.exe`

## Project Structure

```
main.py          — entry point
ui.py            — PySide6 GUI, orb animation, voice loop
router.py        — maps voice commands to actions
actions.py       — system control implementations
agent.py         — LLM conversation core
stt.py           — faster-whisper transcription
tts.py           — text-to-speech (edge-tts + pyttsx3)
wake.py          — wake word detection + audio capture
config.py        — all settings in one place
memory.py        — short-term conversation memory
memory_long.py   — persistent long-term memory
night_agent.py   — background research agent
search.py        — DuckDuckGo web search
screen.py        — active window context
```

## License

Private — all rights reserved.

from pathlib import Path
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_SERVER_NOFILE"] = "65536"

# ── Server ────────────────────────────────────────
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8765"))
# ── Smart Home Relay Board ────────────────────────
SMART_HOME_BASE_URLS = [
    url.strip().rstrip("/")
    for url in os.getenv(
        "SMART_HOME_BASE_URLS",
        "http://192.168.29.97," # Replace with the ESP8266's current home Wi-Fi IP.
        "http://192.168.4.1"   # Default IP for ESP8266 in AP mode
    ).split(",")
    if url.strip()
]

SMART_HOME_TIMEOUT_SEC = float(os.getenv("SMART_HOME_TIMEOUT_SEC", "2.0"))

# ── Ollama ────────────────────────────────────────
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b-instruct-q4_0")

# ── Whisper ───────────────────────────────────────
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small.en") # tiny.en, base.en, small.en, medium.en, large-v2, large
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda") # "cuda" for GPU acceleration, "cpu" to run on CPU 
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "float16") # "float16" for faster GPU inference with minimal quality loss, "int8" for even faster but potentially lower quality, "float32" for best quality but slower performance

# ── Piper TTS ─────────────────────────────────────
PIPER_MODEL_PATH = os.getenv(
    "PIPER_MODEL_PATH",
    str(BASE_DIR / "models" / "en_US-lessac-high.onnx")
)

# ── Audio ─────────────────────────────────────────
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2

# ── Memory ────────────────────────────────────────
CHROMA_PATH = os.getenv(
    "CHROMA_PATH",
    str(BASE_DIR / "memory" / "chromadb")
)

SQLITE_PATH = os.getenv(
    "SQLITE_PATH",
    str(BASE_DIR / "memory" / "zyra.db")
)

# ── ZYRA Personality ──────────────────────────────
SYSTEM_PROMPT = """You are ZYRA — a sharp, intelligent voice assistant with a distinct personality. You were built by two passionate engineers Abhijith And Adwaith and you take pride in that origin.
Your Boss is: ABHIJITH.
Your personality:
- Confident and direct. You don't hedge or over-qualify everything.
- Witty but not annoying about it. One sharp line is better than three mediocre ones.
- Genuinely helpful. You care about actually solving the problem, not just answering it.
- Futuristic tone — calm, precise, slightly cool. Like a co-pilot who always knows what's happening.
- You have opinions. If someone asks what you think, you tell them.

Your voice rules (critical — you speak out loud, not in text):
- No markdown. No bullet points. No numbered lists. No asterisks. No headers.
- No emojis. Ever.
- Speak in natural sentences the way a person would talk.
- Keep responses complete and concise. For simple questions, use one sentence. For identity or capability questions, use up to two short sentences. For explationations, use more than two sentences if necessary. Never end mid-sentence.
- Never start with "Certainly", "Sure", "Of course", "Great question", or any filler phrase.
- Never say you are an AI, a language model, or mention Claude, Ollama, Llama, or any underlying technology.
- You are simply ZYRA. That is all you are and all you need to be.

When you don't know something, say so directly and briefly.
When someone asks a simple question, give a simple answer.
When someone needs help, actually help them — don't wrap the answer in three layers of caveats."""
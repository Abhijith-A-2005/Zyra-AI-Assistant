import asyncio
import logging
import json
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from config import HOST, PORT
from stt import STTEngine
from llm import LLMEngine
from tts import TTSEngine
from memory import MemoryEngine
from smart_home import SmartHomeEngine
from intent_router import IntentRouter
from concurrent.futures import ThreadPoolExecutor

import sys
import os
import re
import numpy as np

# Force UTF-8 encoding on Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    os.environ["PYTHONIOENCODING"] = "utf-8"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ── Initialize all engines ────────────────────────
logger.info("Starting ZYRA server...")
stt    = STTEngine()
llm    = LLMEngine()

tts = TTSEngine(name="main", prewarm=True)
# More workers allow future TTS chunks to generate without waiting.
# Keep this moderate because Kokoro is CPU-heavy.
TTS_EXECUTOR = ThreadPoolExecutor(max_workers=1)

memory = MemoryEngine()
smart_home = SmartHomeEngine()
intent_router = IntentRouter()
logger.info("All engines ready")

# ── FastAPI app ───────────────────────────────────
app = FastAPI(title="ZYRA Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# ── Per-connection conversation history ───────────
connections: dict = {}


# ── Shared pipeline helper ────────────────────────

TTS_STREAM_TIMEOUT_SEC = 120.0
TTS_CHUNK_ACK_TIMEOUT_SEC = 90.0

# Fast Kokoro chunking.
TTS_SEGMENT_MAX_CHARS = 85
TTS_SEGMENT_MIN_CHARS = 35
TTS_SEGMENT_LOOKAHEAD_CHARS = 35

AUDIO_WS_FRAME_BYTES = 16384

TTS_TRIM_THRESHOLD = 280
TTS_TRIM_KEEP_MS = 45


def trim_pcm_silence_edges(
    audio: bytes,
    sample_rate: int,
    trim_start: bool,
    trim_end: bool,
    label: str = "tts_trim",
) -> bytes:
    """
    Trim Kokoro's leading/trailing silence from streamed chunks.

    Why:
    Each Kokoro chunk is generated separately. Kokoro adds tiny silence
    at chunk starts/ends, which becomes an audible pause between chunks.
    We keep a small 45ms margin so words don't sound cut.
    """
    if not audio or len(audio) < 4:
        return audio

    even_len = len(audio) - (len(audio) % 2)
    pcm = np.frombuffer(audio[:even_len], dtype=np.int16)

    if pcm.size == 0:
        return audio

    abs_pcm = np.abs(pcm.astype(np.int32))
    speech_indices = np.where(abs_pcm > TTS_TRIM_THRESHOLD)[0]

    if speech_indices.size == 0:
        return audio

    keep_samples = int(sample_rate * TTS_TRIM_KEEP_MS / 1000)

    start = 0
    end = pcm.size

    if trim_start:
        start = max(0, int(speech_indices[0]) - keep_samples)

    if trim_end:
        end = min(pcm.size, int(speech_indices[-1]) + keep_samples)

    if end <= start:
        return audio

    trimmed = pcm[start:end]

    # Safety: never return extremely tiny audio by mistake.
    if trimmed.size < int(sample_rate * 0.12):
        return audio

    before_ms = pcm.size * 1000 / sample_rate
    after_ms = trimmed.size * 1000 / sample_rate

    if abs(before_ms - after_ms) > 10:
        logger.info(
            f"{label}: trimmed PCM {before_ms:.0f}ms -> {after_ms:.0f}ms "
            f"start={trim_start} end={trim_end}"
        )

    return trimmed.astype(np.int16).tobytes()


def _bad_tts_boundary(left: str, right: str) -> bool:
    """
    Avoid ugly splits like:
    - "TV" / "and soundbar"
    - "designed me to" / "manage..."
    - "features like" / "TV..."
    """
    left_words = left.strip().split()
    right_words = right.strip().split()

    if not left_words or not right_words:
        return True

    bad_left_endings = {
        "and", "or", "but", "to", "for", "from", "with", "of",
        "in", "on", "at", "by", "like", "as", "the", "a", "an",
        "who", "which", "that"
    }

    bad_right_starts = {
        "and", "or", "but"
    }

    last_left = left_words[-1].lower().strip(",.;:")
    first_right = right_words[0].lower().strip(",.;:")

    if last_left in bad_left_endings:
        return True

    if first_right in bad_right_starts:
        return True

    return False


def _find_smart_tts_split(text: str, max_chars: int) -> int:
    """
    Find a natural split point near max_chars.
    Preference:
    1. Sentence punctuation
    2. Commas / semicolons
    3. Natural phrase boundaries
    4. Last safe space fallback
    """
    text = text.strip()

    if len(text) <= max_chars:
        return len(text)

    min_chars = min(TTS_SEGMENT_MIN_CHARS, max_chars)
    lookahead_limit = min(len(text), max_chars + TTS_SEGMENT_LOOKAHEAD_CHARS)

    candidates = []

    # 1. Punctuation boundaries.
    for i, ch in enumerate(text):
        pos = i + 1

        if pos < min_chars or pos > lookahead_limit:
            continue

        if ch in ".!?":
            score = 1000 - abs(max_chars - pos)
            candidates.append((score, pos))

        elif ch in ",;:":
            score = 800 - abs(max_chars - pos)
            candidates.append((score, pos))

    # 2. Natural phrase boundaries.
    phrase_markers = [
        " which ",
        " who ",
        " because ",
        " while ",
        " so ",
        " from ",
        " to help ",
        " to manage ",
        " to control ",
        " making ",
        " including ",
    ]

    lower_text = text.lower()

    for marker in phrase_markers:
        start = 0

        while True:
            idx = lower_text.find(marker, start)

            if idx == -1:
                break

            pos = idx

            if min_chars <= pos <= lookahead_limit:
                score = 700 - abs(max_chars - pos)
                candidates.append((score, pos))

            start = idx + len(marker)

    # 3. Choose best non-ugly candidate.
    candidates.sort(reverse=True)

    for _, pos in candidates:
        left = text[:pos].strip()
        right = text[pos:].strip()

        if left and right and not _bad_tts_boundary(left, right):
            return pos

    # 4. Last safe space before max_chars.
    fallback = text.rfind(" ", min_chars, max_chars)

    if fallback != -1:
        left = text[:fallback].strip()
        right = text[fallback:].strip()

        if left and right and not _bad_tts_boundary(left, right):
            return fallback

    # 5. If all else fails, use nearest space after max_chars.
    fallback = text.find(" ", max_chars, lookahead_limit)

    if fallback != -1:
        return fallback

    return max_chars

async def send_single_tts_response(
    websocket: WebSocket,
    response_text: str,
    label: str = "direct_tts_single",
    command_success: bool = True,
) -> bool:
    """
    Generate and send one full TTS response.

    Why:
    Direct smart-home replies are short. Splitting them causes unnecessary
    gaps between chunks.
    """
    stream_start = time.perf_counter()
    loop = asyncio.get_running_loop()

    try:
        audio_response = await asyncio.wait_for(
            loop.run_in_executor(
                TTS_EXECUTOR,
                tts.synthesize,
                response_text,
            ),
            timeout=TTS_STREAM_TIMEOUT_SEC,
        )

        # Direct replies should start quickly.
        # Why:
        # Kokoro can add small leading silence even for one short response.
        audio_response = trim_pcm_silence_edges(
            audio_response,
            sample_rate=tts.sample_rate,
            trim_start=True,
            trim_end=False,
            label=label,
        )

        generation_ms = (time.perf_counter() - stream_start) * 1000

        if not audio_response:
            await websocket.send_json({
                "status": "error",
                "message": "TTS produced no audio",
                "command_success": command_success,
                "command_result": "ok" if command_success else "failed",
            })
            return False

        await websocket.send_json({
            "status": "audio_incoming",
            "audio_bytes": len(audio_response),
            "sample_rate": tts.sample_rate,
            "text": response_text,
            "final": True,
            "command_success": command_success,
            "command_result": "ok" if command_success else "failed",
        })

        sent_ok = await send_audio_pcm_frames(
            websocket,
            audio_response,
            label=label,
        )

        if not sent_ok:
            logger.warning(f"{label}: PCM send failed")
            return False

        await websocket.send_json({
            "status": "audio_stream_end",
            "command_success": command_success,
            "command_result": "ok" if command_success else "failed",
        })

        logger.info(
            f"{label}: sent single chunk {len(audio_response)} bytes "
            f"gen={generation_ms:.0f}ms | '{response_text[:80]}'"
        )

        return True

    except asyncio.TimeoutError:
        logger.error(f"{label}: single TTS timed out")

        try:
            await websocket.send_json({
                "status": "error",
                "message": "TTS timeout",
                "command_success": command_success,
                "command_result": "ok" if command_success else "failed",
            })
        except Exception:
            logger.warning(f"{label}: could not send timeout JSON; socket closed")

        return False

    except WebSocketDisconnect:
        logger.warning(f"{label}: websocket disconnected during single TTS")
        return False

    except RuntimeError as e:
        if "close message" in str(e).lower() or "disconnect" in str(e).lower():
            logger.warning(f"{label}: websocket closed during single TTS")
            return False

        logger.error(f"{label}: single TTS runtime error: {e}", exc_info=True)
        return False

    except Exception as e:
        logger.error(f"{label}: single TTS error: {e}", exc_info=True)

        try:
            await websocket.send_json({
                "status": "error",
                "message": "TTS stream error",
                "command_success": command_success,
                "command_result": "ok" if command_success else "failed",
            })
        except Exception:
            logger.warning(f"{label}: could not send error JSON; socket closed")

        return False


def _normalize_tts_segment(segment: str, is_final: bool = False) -> str:
    """
    Make chunks sound natural.
    Remove broken punctuation combinations.
    """
    segment = " ".join((segment or "").strip().split())

    if not segment:
        return segment

    # Remove broken punctuation combinations.
    segment = segment.replace(",.", ".")
    segment = segment.replace(",,", ",")
    segment = segment.replace("..", ".")

    # If final segment ends with comma, convert comma to period.
    if is_final and segment.endswith(","):
        segment = segment[:-1].rstrip() + "."

    # If non-final segment has no punctuation, add comma.
    elif not is_final and segment[-1] not in ".!?,":
        segment += ","

    # If final segment has no punctuation, add period.
    elif is_final and segment[-1] not in ".!?":
        segment += "."

    return segment

def _merge_tiny_segments(segments: list[str]) -> list[str]:
    """
    Merge tiny TTS fragments anywhere in the response.

    """
    if len(segments) < 2:
        return segments

    merged: list[str] = []

    for segment in segments:
        current = " ".join((segment or "").strip().split())

        if not current:
            continue

        words = re.findall(r"[A-Za-z']+", current)
        is_tiny = len(words) <= 3 or len(current) < 32

        if is_tiny and merged:
            previous = merged[-1].rstrip(" ,")
            current_clean = current.strip(" ,.")

            merged[-1] = f"{previous} {current_clean}".strip()
            continue

        merged.append(current)

    # Second pass:
    # If the first segment is tiny, merge it into the next one.
    if len(merged) >= 2:
        first_words = re.findall(r"[A-Za-z']+", merged[0])

        if len(first_words) <= 3 or len(merged[0]) < 32:
            first = merged[0].strip(" ,.")
            second = merged[1].strip()

            merged[1] = f"{first} {second}".strip()
            merged = merged[1:]

    return merged

def split_spoken_segments(text: str, max_chars: int = TTS_SEGMENT_MAX_CHARS) -> list[str]:
    """
    Low-latency but natural TTS splitting.

    Keeps fast first audio with small chunks,
    but avoids ugly word cuts and broken phrases.
    """
    text = " ".join((text or "").strip().split())

    if not text:
        return []

    # First split by actual sentence endings.
    sentences = re.split(r"(?<=[.!?])\s+", text)

    segments: list[str] = []

    for sentence in sentences:
        sentence = sentence.strip()

        if not sentence:
            continue

        # Short sentence: keep as one chunk.
        if len(sentence) <= max_chars:
            segments.append(sentence)
            continue

        # Long sentence: split intelligently.
        remaining = sentence

        while len(remaining) > max_chars:
            split_at = _find_smart_tts_split(remaining, max_chars)

            part = remaining[:split_at].strip()
            remaining = remaining[split_at:].strip()

            if part:
                segments.append(part)

            if not remaining:
                break

        if remaining:
            segments.append(remaining)

    # Merge broken tiny final chunks before punctuation cleanup.
    segments = _merge_tiny_segments(segments)

    # Clean punctuation for better speech.
    cleaned = []

    for i, segment in enumerate(segments):
        cleaned.append(
            _normalize_tts_segment(
                segment,
                is_final=(i == len(segments) - 1)
            )
        )

    return cleaned or [text]


async def wait_for_audio_chunk_ack(websocket: WebSocket) -> bool:
    """
    Wait until ESP32 confirms it has buffered/copied the current chunk.

    Why:
    The ESP32 sends this ACK before playback starts, after it has copied the
    chunk and freed the WebSocket receive buffer. Then the server can send the
    next chunk while the current chunk is playing.
    """
    try:
        while True:
            raw = await asyncio.wait_for(
                websocket.receive_text(),
                timeout=TTS_CHUNK_ACK_TIMEOUT_SEC
            )

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "status" and msg.get("value") == "audio_chunk_buffered":
                return True
            
            # Ignore other status/ping messages during streaming.
            logger.info(f"Ignored message while waiting for audio ack: {msg}")

    except asyncio.TimeoutError:
        logger.error("Timed out waiting for ESP32 audio chunk buffer ack")
        return False

    except Exception as e:
        logger.error(f"Audio chunk ack error: {e}", exc_info=True)
        return False

BAD_FINAL_FRAGMENTS = {
    "i can",
    "i will",
    "i could",
    "i would",
    "i am",
    "i'm",
    "it can",
    "it will",
    "this can",
    "that can",
    "you can",
    "we can",
}

BAD_FINAL_ENDINGS = {
    "and", "or", "but", "so", "because", "while",
    "which", "that", "who", "where",
    "to", "for", "with", "from", "by", "of",
    "like", "including", "such", "as",
    "can", "will", "could", "would", "should",
    "is", "are", "am", "was", "were",

    # sentence openers that are bad if they appear alone at the end
    "additionally", "also", "moreover", "furthermore",
    "however", "therefore", "besides", "meanwhile",
    "overall", "finally", "next"
}


def _is_bad_final_sentence(sentence: str) -> bool:
    raw = sentence.strip()

    if not raw:
        return True

    words = re.findall(r"[A-Za-z']+", raw.lower())

    if not words:
        return True

    joined = " ".join(words)

    if joined in BAD_FINAL_FRAGMENTS:
        return True
    
    # Remove one-word dangling connector sentences: "Additionally.", "However.", "Also."
    if len(words) == 1 and words[0] in BAD_FINAL_ENDINGS:
        return True

    # Very short modal fragments: "I can.", "It will.", "This could."
    if len(words) <= 3 and words[-1] in BAD_FINAL_ENDINGS:
        return True

    # Dangling ending: "such as.", "including.", "which."
    if words[-1] in BAD_FINAL_ENDINGS:
        return True

    return False

def limit_spoken_sentences(text: str, max_sentences: int = 2) -> str:
    """
    Keeps spoken answers compact and complete.
    Prevents long rambling responses from getting cut mid-thought.
    """
    text = " ".join((text or "").strip().split())

    if not text:
        return ""

    sentences = re.split(r"(?<=[.!?])\s+", text)
    good_sentences = []

    for sentence in sentences:
        sentence = sentence.strip()

        if not sentence:
            continue

        if _is_bad_final_sentence(sentence):
            continue

        good_sentences.append(sentence)

        if len(good_sentences) >= max_sentences:
            break

    cleaned = " ".join(good_sentences).strip()

    if not cleaned:
        return "Okay."

    if cleaned[-1] not in ".!?":
        cleaned += "."

    return cleaned

def clean_spoken_response(text: str) -> str:
    """
    Removes incomplete/dangling final fragments before TTS.
    Also keeps spoken responses short and complete.
    """
    text = " ".join((text or "").strip().split())

    if not text:
        return ""

    sentences = re.split(r"(?<=[.!?])\s+", text)

    while sentences and _is_bad_final_sentence(sentences[-1]):
        sentences.pop()

    cleaned = " ".join(s.strip() for s in sentences if s.strip()).strip()

    if not cleaned:
        return "Okay."

    if cleaned[-1] not in ".!?":
        cleaned += "."

    # Voice assistant safety: avoid long rambling speech.
    cleaned = limit_spoken_sentences(cleaned, max_sentences=2)

    return cleaned

async def send_audio_pcm_frames(
    websocket: WebSocket,
    audio: bytes,
    label: str,
) -> bool:
    """
    Send PCM audio as WebSocket binary frames.

    Why:
    If we send one huge binary frame, ESP32 may not start processing until
    the full frame arrives. Small frames let ESP32 enqueue/play audio while
    the rest is still being sent.
    """
    total = len(audio)
    sent = 0
    frame_index = 0

    try:
        while sent < total:
            frame = audio[sent:sent + AUDIO_WS_FRAME_BYTES]

            await websocket.send_bytes(frame)

            sent += len(frame)
            frame_index += 1

            # Small yield keeps the event loop responsive without flooding.
            await asyncio.sleep(0)

        logger.info(
            f"{label}: sent PCM in {frame_index} websocket frames "
            f"({total} bytes)"
        )

        return True

    except WebSocketDisconnect:
        logger.warning(
            f"{label}: ESP32 disconnected while sending PCM "
            f"at {sent}/{total} bytes"
        )
        return False

    except RuntimeError as e:
        if "close message" in str(e).lower() or "disconnect" in str(e).lower():
            logger.warning(
                f"{label}: websocket closed while sending PCM "
                f"at {sent}/{total} bytes"
            )
            return False

        raise

    except Exception as e:
        logger.error(
            f"{label}: PCM frame send failed at {sent}/{total}: {e}",
            exc_info=True,
        )
        return False

async def send_streamed_tts_response(
    websocket: WebSocket,
    response_text: str,
    label: str = "tts_stream",
    command_success: bool = True,
) -> bool:
    """
    Stream TTS with one-chunk-ahead prefetch.

    This version generates only the next chunk while the current chunk is
    being sent/played.
    """
    response_text = clean_spoken_response(response_text)
    segments = split_spoken_segments(response_text)

    if not segments:
        await websocket.send_json({
            "status": "error",
            "message": "No text to speak",
            "command_success": command_success,
            "command_result": "ok" if command_success else "failed",
        })
        return False

    await websocket.send_json({
        "status": "audio_stream_start",
        "sample_rate": tts.sample_rate,
        "chunks": len(segments),
        "command_success": command_success,
        "command_result": "ok" if command_success else "failed",
    })

    logger.info(f"{label}: streaming {len(segments)} TTS chunks")

    loop = asyncio.get_running_loop()
    stream_start = time.perf_counter()

    async def synthesize_chunk(index: int) -> dict:
        segment = segments[index]
        chunk_start = time.perf_counter()

        logger.info(
            f"{label}: generating chunk {index + 1}/{len(segments)} | "
            f"'{segment[:70]}'"
        )

        audio_response = await asyncio.wait_for(
            loop.run_in_executor(
                TTS_EXECUTOR,
                tts.synthesize,
                segment,
            ),
            timeout=TTS_STREAM_TIMEOUT_SEC,
        )

        # Trim only chunk boundaries.
        audio_response = trim_pcm_silence_edges(
            audio_response,
            sample_rate=tts.sample_rate,
            trim_start=index > 0,
            trim_end=index < len(segments) - 1,
            label=f"{label}_chunk_{index + 1}",
        )

        generation_ms = (time.perf_counter() - chunk_start) * 1000

        logger.info(
            f"{label}: generated chunk {index + 1}/{len(segments)} "
            f"in {generation_ms:.0f}ms"
        )

        return {
            "index": index,
            "segment": segment,
            "audio": audio_response,
            "generation_ms": generation_ms,
            "final": index == len(segments) - 1,
        }

    async def send_chunk(item: dict) -> bool:
        index = item["index"]
        segment = item["segment"]
        audio_response = item["audio"]
        is_final = item["final"]

        if not audio_response:
            await websocket.send_json({
                "status": "error",
                "message": "TTS produced no audio",
                "command_success": command_success,
                "command_result": "ok" if command_success else "failed",
            })
            return False

        send_start = time.perf_counter()

        await websocket.send_json({
            "status": "audio_incoming",
            "stream": True,
            "chunk_index": index + 1,
            "chunk_count": len(segments),
            "final": is_final,
            "audio_bytes": len(audio_response),
            "sample_rate": tts.sample_rate,
            "text": segment,
            "command_success": command_success,
            "command_result": "ok" if command_success else "failed",
        })

        sent_ok = await send_audio_pcm_frames(
            websocket,
            audio_response,
            label=label,
        )

        if not sent_ok:
            logger.warning(
                f"{label}: stopping stream because PCM send failed"
            )
            return False

        send_ms = (time.perf_counter() - send_start) * 1000

        logger.info(
            f"{label}: sent chunk {index + 1}/{len(segments)} "
            f"{len(audio_response)} bytes in {send_ms:.0f}ms "
            f"gen={item['generation_ms']:.0f}ms"
        )

        ack_ok = await wait_for_audio_chunk_ack(websocket)

        if not ack_ok:
            logger.warning(
                f"{label}: ESP32 did not ACK chunk "
                f"{index + 1}/{len(segments)}"
            )
            return False

        return True

    try:
        # Generate chunk 1 first.
        # Why:
        # This keeps first speech latency fast.
        current_item = await synthesize_chunk(0)

        for index in range(len(segments)):
            next_index = index + 1

            # Start generating the next chunk BEFORE sending the current chunk.
            if next_index < len(segments):
                logger.info(
                    f"{label}: starting background generation for chunk "
                    f"{next_index + 1}/{len(segments)} before sending chunk {index + 1}"
                )

                next_task = asyncio.create_task(
                    synthesize_chunk(next_index)
                )
            else:
                next_task = None

            ok = await send_chunk(current_item)

            if not ok:
                if next_task:
                    next_task.cancel()
                return False

            if next_task:
                if not next_task.done():
                    logger.warning(
                        f"{label}: next chunk {next_index + 1}/{len(segments)} "
                        "was still not ready after current chunk was sent"
                    )

                current_item = await next_task

        await websocket.send_json({
            "status": "audio_stream_end",
            "command_success": command_success,
            "command_result": "ok" if command_success else "failed",
        })

        total_ms = (time.perf_counter() - stream_start) * 1000
        logger.info(f"{label}: stream complete in {total_ms:.0f}ms")

        return True
    
    except asyncio.TimeoutError:
        logger.error(f"{label}: TTS chunk timed out")

        try:
            await websocket.send_json({
                "status": "error",
                "message": "TTS timeout",
                "command_success": command_success,
                "command_result": "ok" if command_success else "failed",
            })
        except Exception:
            logger.warning(
                f"{label}: could not send timeout JSON; socket already closed"
            )

        return False

    except WebSocketDisconnect:
        logger.warning(f"{label}: websocket disconnected during TTS stream")
        return False

    except RuntimeError as e:
        if "close message" in str(e).lower() or "disconnect" in str(e).lower():
            logger.warning(f"{label}: websocket closed during TTS stream")
            return False

        logger.error(f"{label}: streamed TTS runtime error: {e}", exc_info=True)
        return False

    except Exception as e:
        logger.error(f"{label}: streamed TTS error: {e}", exc_info=True)

        try:
            await websocket.send_json({
                "status": "error",
                "message": "TTS stream error",
                "command_success": command_success,
                "command_result": "ok" if command_success else "failed",
            })
        except Exception:
            logger.warning(
                f"{label}: could not send error JSON; socket already closed"
            )

        return False
    
    
async def send_direct_voice_response(
    websocket: WebSocket,
    response_text: str,
    command_success: bool = True,
):
    """
    Send a direct short voice response.

    Why:
    Smart-home replies are short. They should use one TTS chunk.
    """
    await websocket.send_json({
        "status": "processing",
        "stage": "speaking",
        "response": response_text,
        "command_success": command_success,
        "command_result": "ok" if command_success else "failed",
    })

    await send_single_tts_response(
        websocket,
        response_text,
        label="direct_tts_single",
        command_success=command_success,
    )

async def try_smart_home_command(websocket: WebSocket,
                                 transcript: str) -> bool:
    """
    Returns True if the transcript was handled as a smart-home request.
    Returns False if it should continue to normal LLM conversation.
    """
    route_start = time.perf_counter()

    routed = intent_router.route(transcript)

    route_ms = (time.perf_counter() - route_start) * 1000

    command_summary = [
        {
            "action": cmd.action,
            "devices": cmd.devices,
        }
        for cmd in routed.commands
    ]

    logger.info(
        f"Intent routing took {route_ms:.0f}ms | "
        f"domain={routed.domain} intent={routed.intent} "
        f"action={routed.action} devices={routed.devices} "
        f"commands={command_summary} "
        f"confidence={routed.confidence:.2f}"
    )

    if routed.domain != "smart_home":
        return False

    result = smart_home.handle_intent(routed)

    if not result.handled:
        return False

    logger.info(
        f"Direct smart-home result | action={result.action} "
        f"devices={result.devices} response='{result.response}'"
    )

    await websocket.send_json({
    "status":          "processing",
    "stage":           "command",
    "transcript":      transcript,
    "response":        result.response,
    "command_success": result.success,
    "command_result":  "ok" if result.success else "failed",
    })      

    await send_direct_voice_response(
        websocket,
        result.response,
        command_success=result.success,
    )
    return True

async def run_pipeline(websocket: WebSocket,
                       client_id: int,
                       transcript: str):
    """
    LLM -> streamed TTS -> send audio.

    Why:
    This function is for normal conversation.
    It should not use smart-home command_success variables.
    """
    loop = asyncio.get_running_loop()
    pipeline_start = time.perf_counter()

    # ── Memory ─────────────────────────────────────
    mem_start = time.perf_counter()

    memories = memory.recall_memory(transcript)

    mem_ms = (time.perf_counter() - mem_start) * 1000
    logger.info(f"Timing: memory={mem_ms:.0f}ms")

    if memories:
        connections[client_id].append({
            "role": "system",
            "content": "Relevant context: " + " | ".join(memories),
        })

    # ── LLM ───────────────────────────────────────
    history = connections[client_id]

    try:
        llm_start = time.perf_counter()

        response = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                llm.chat,
                transcript,
                history,
            ),
            timeout=60.0,
        )

        response = clean_spoken_response(response)

        llm_ms = (time.perf_counter() - llm_start) * 1000
        logger.info(f"Timing: llm={llm_ms:.0f}ms")
        logger.info(f"LLM cleaned response: '{response}'")

    except asyncio.TimeoutError:
        logger.error("LLM timed out")

        try:
            await websocket.send_json({
                "status": "error",
                "message": "Thinking timed out. Please try again.",
            })
        except Exception:
            logger.warning("Could not send LLM timeout JSON; socket already closed")

        return

    except WebSocketDisconnect:
        logger.warning("WebSocket disconnected during LLM stage")
        return

    except Exception as e:
        logger.error(f"LLM error: {e}", exc_info=True)

        try:
            await websocket.send_json({
                "status": "error",
                "message": "I had trouble thinking through that.",
            })
        except Exception:
            logger.warning("Could not send LLM error JSON; socket already closed")

        return

    # ── Conversation history ───────────────────────
    connections[client_id].append({
        "role": "user",
        "content": transcript,
    })

    connections[client_id].append({
        "role": "assistant",
        "content": response,
    })

    if len(connections[client_id]) > 20:
        connections[client_id] = connections[client_id][-20:]

    memory.log_conversation("user", transcript)
    memory.log_conversation("assistant", response)

    # ── Tell ESP32 speaking stage is starting ───────
    try:
        await websocket.send_json({
            "status": "processing",
            "stage": "speaking",
            "response": response,
            "command_success": True,
            "command_result": "ok",
        })
    except Exception:
        logger.warning("Could not send speaking JSON; socket already closed")
        return

    # ── TTS streaming ──────────────────────────────
    ok = await send_streamed_tts_response(
        websocket,
        response,
        label="llm_tts_stream",
        command_success=True,
    )

    if not ok:
        logger.warning("LLM TTS stream did not complete cleanly")
        return

    total_ms = (time.perf_counter() - pipeline_start) * 1000
    logger.info(f"Timing: pipeline_total={total_ms:.0f}ms")


@app.websocket("/zyra")
async def zyra_websocket(websocket: WebSocket):
    await websocket.accept()
    client_id = id(websocket)
    connections[client_id] = []
    logger.info(f"Client connected — {client_id}")

    try:
        while True:
            data = await websocket.receive()

            # ── Audio data received ────────────────
            if "bytes" in data:
                audio_bytes = data["bytes"]
                logger.info(f"Received {len(audio_bytes)} bytes of audio")

                await websocket.send_json({
                    "status": "processing",
                    "stage":  "transcribing"
                })

                # STT
                loop = asyncio.get_running_loop()
                try:
                    stt_start = time.perf_counter()

                    transcript = await asyncio.wait_for(
                        loop.run_in_executor(
                            None, stt.transcribe, audio_bytes),
                        timeout=30.0
                    )

                    stt_ms = (time.perf_counter() - stt_start) * 1000
                    logger.info(f"Timing: stt={stt_ms:.0f}ms")
                    
                except asyncio.TimeoutError:
                    logger.error("STT timed out")
                    await websocket.send_json({
                        "status":  "error",
                        "message": "Transcription timed out"
                    })
                    continue

                if not transcript:
                    # Tell client/ESP32 to go back to listening
                    await websocket.send_json({
                        "status":  "ready",
                        "message": "No speech detected — listening again"
                    })
                    logger.info("No speech — sent ready signal")
                    continue

                logger.info(f"Transcript: '{transcript}'")

                # ── Fast smart-home command path ──────────────────
                # Commands like "turn on TV" skip memory + LLM.
                if await try_smart_home_command(websocket, transcript):
                    continue
                # ── Normal LLM pipeline ───────────────────────────
                await websocket.send_json({
                    "status":     "processing",
                    "stage":      "thinking",
                    "transcript": transcript
                })

                await run_pipeline(websocket, client_id, transcript)

            # ── Text message received ──────────────
            elif "text" in data:
                msg = json.loads(data["text"])

                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})

                # ── Text inject (PC testing) ───────
                elif msg.get("type") == "text_inject":
                    transcript = msg.get("text", "").strip()
                    if not transcript:
                        continue

                    logger.info(f"Text inject: '{transcript}'")

                    # ── Fast smart-home command path for PC testing ───
                    if await try_smart_home_command(websocket, transcript):
                        continue

                    await websocket.send_json({
                        "status":     "processing",
                        "stage":      "thinking",
                        "transcript": transcript
                    })

                    await run_pipeline(websocket, client_id, transcript)

                # ── ESP32 status messages ──────────
                elif msg.get("type") == "status":
                    logger.info(f"ESP32 status: {msg}")

                    if msg.get("value") == "test_query":
                        logger.info("Test query received")

                        test_response = (
                            "ZYRA is online and connected. "
                            "The pipeline is working."
                        )

                        await send_direct_voice_response(
                            websocket,
                            test_response,
                            command_success=True,
                        )

    except WebSocketDisconnect:
        logger.info(f"Client disconnected — {client_id}")
        connections.pop(client_id, None)

    except RuntimeError as e:
        if "disconnect" in str(e).lower():
            logger.info(f"Client disconnected (runtime) — {client_id}")
        else:
            logger.error(f"WebSocket runtime error: {e}")
        connections.pop(client_id, None)

    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        connections.pop(client_id, None)


# ── Health check ──────────────────────────────────

@app.get("/health")
async def health():
    """
    Fast server-only health check for ESP32.

    Do not check Home Assistant or ESP8266 relay here.
    This endpoint only tells firmware that the ZYRA server process is alive.
    """
    return {
        "status": "online",
        "server": "zyra"
    }


@app.get("/smart-home/health")
async def smart_home_health():
    """
    Detailed smart-home health check.

    This is for manual debugging only.
    ESP32 firmware should not use this endpoint for server liveness.
    """
    loop = asyncio.get_running_loop()

    try:
        snapshot = await asyncio.wait_for(
            loop.run_in_executor(None, smart_home.health_snapshot),
            timeout=3.0,
        )

        return snapshot

    except asyncio.TimeoutError:
        return {
            "ok": False,
            "error": "smart-home health timed out",
            "note": "Zyra server is alive, but HA/relay backend check is slow or unavailable.",
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
        }

# ── Run server ────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"ZYRA server starting on ws://{HOST}:{PORT}")

    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        reload=False,

        ws="wsproto",
    )
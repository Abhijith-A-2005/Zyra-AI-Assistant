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
# Second CPU Kokoro engine for background prefetch.
# This lets chunk 2 generate while chunk 1 is still generating/playing.
tts_prefetch = TTSEngine(name="prefetch", prewarm=False)
TTS_EXECUTOR = ThreadPoolExecutor(max_workers=2)

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
TTS_SEGMENT_MAX_CHARS = 120
TTS_SEGMENT_MIN_CHARS = 45
TTS_SEGMENT_LOOKAHEAD_CHARS = 40
TTS_READY_QUEUE_SIZE = 6


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

def _merge_tiny_final_segment(segments: list[str]) -> list[str]:
    """
    Avoid final fragments like:
    - 'subwoofer,'
    - 'I can.'
    - 'and rear speakers,'
    """
    if len(segments) < 2:
        return segments

    last = segments[-1].strip()
    last_words = re.findall(r"[A-Za-z']+", last)

    # Merge tiny final tail into previous chunk.
    if len(last_words) <= 3 or len(last) < 28:
        previous = segments[-2].rstrip(" ,")
        last_clean = last.strip(" ,.")

        merged = f"{previous} {last_clean}".strip()

        return segments[:-2] + [merged]

    return segments

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
    segments = _merge_tiny_final_segment(segments)

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
    Wait until ESP32 confirms it has played the current chunk.
    This prevents the server from sending the next chunk before the
    firmware has freed the previous audio buffer.
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

async def send_streamed_tts_response(
    websocket: WebSocket,
    response_text: str,
    label: str = "tts_stream",
) -> bool:
    """
    Low-latency smooth streamed TTS.

    Goal:
    - Do NOT delay speech start by waiting for multiple chunks.
    - Generate chunk 1 and send it immediately.
    - While ESP32 plays chunk 1, generate future chunks in the background.
    - Keep ready chunks in a server-side queue.
    - ESP32 still receives only one chunk ahead safely.
    """
    loop = asyncio.get_running_loop()
    response_text = clean_spoken_response(response_text)
    segments = split_spoken_segments(response_text)

    if not segments:
        await websocket.send_json({
            "status": "error",
            "message": "No text to speak"
        })
        return False

    await websocket.send_json({
        "status": "audio_stream_start",
        "sample_rate": tts.sample_rate,
        "chunks": len(segments),
    })

    logger.info(f"{label}: streaming {len(segments)} TTS chunks")

    stream_start = time.perf_counter()
    ready_queue: asyncio.Queue = asyncio.Queue(maxsize=TTS_READY_QUEUE_SIZE)

    async def synthesize_one(index: int, segment: str) -> dict:
        chunk_start = time.perf_counter()

        # Chunk 1 uses the main TTS engine.
        # Later chunks use the prefetch TTS engine.
        # This allows chunk 2 generation to run in parallel with chunk 1.
        engine = tts if index == 0 else tts_prefetch

        audio_response = await asyncio.wait_for(
            loop.run_in_executor(TTS_EXECUTOR, engine.synthesize, segment),
            timeout=TTS_STREAM_TIMEOUT_SEC
        )

        generation_ms = (time.perf_counter() - chunk_start) * 1000

        return {
            "index": index,
            "segment": segment,
            "audio": audio_response,
            "final": index == len(segments) - 1,
            "generation_ms": generation_ms,
        }

    async def producer_from_second_chunk():
        """
        Generate remaining chunks continuously in the background.
        This does not delay the first spoken chunk.
        """
        try:
            for index in range(1, len(segments)):
                segment = segments[index]

                try:
                    item = await synthesize_one(index, segment)

                    logger.info(
                        f"{label}: prefetched chunk {index + 1}/{len(segments)} "
                        f"in {item['generation_ms']:.0f}ms | '{segment[:60]}'"
                    )

                    await ready_queue.put(item)

                except asyncio.TimeoutError:
                    logger.error(f"{label}: TTS chunk {index + 1} timed out")
                    await ready_queue.put({
                        "error": "TTS chunk timeout",
                        "index": index,
                    })
                    return

                except Exception as e:
                    logger.error(
                        f"{label}: TTS chunk {index + 1} failed: {e}",
                        exc_info=True
                    )
                    await ready_queue.put({
                        "error": str(e),
                        "index": index,
                    })
                    return

        except asyncio.CancelledError:
            logger.info(f"{label}: TTS prefetch producer cancelled")
            raise

    async def send_chunk(item: dict) -> bool:
        if item.get("error"):
            await websocket.send_json({
                "status": "error",
                "message": item["error"]
            })
            return False

        index = item["index"]
        segment = item["segment"]
        audio_response = item["audio"]
        is_final = item["final"]

        if not audio_response:
            logger.warning(f"{label}: empty TTS chunk {index + 1}")
            return True

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
        })

        await websocket.send_bytes(audio_response)

        send_ms = (time.perf_counter() - send_start) * 1000

        logger.info(
            f"{label}: sent chunk {index + 1}/{len(segments)} "
            f"{len(audio_response)} bytes in {send_ms:.0f}ms "
            f"gen={item['generation_ms']:.0f}ms | '{segment[:60]}'"
        )

        ack_ok = await wait_for_audio_chunk_ack(websocket)

        if not ack_ok:
            return False

        return True

    producer_task = None
    sent_any_audio = False

    try:
        # Start prefetching future chunks immediately.
        # This begins chunk 2 generation while chunk 1 is still being generated.
        if len(segments) > 1:
            producer_task = asyncio.create_task(producer_from_second_chunk())

        # Generate first chunk with the main engine.
        # Speech still starts as soon as chunk 1 is ready.
        first_item = await synthesize_one(0, segments[0])

        logger.info(
            f"{label}: first chunk ready in "
            f"{first_item['generation_ms']:.0f}ms"
        )

        ok = await send_chunk(first_item)

        if not ok:
            if producer_task:
                producer_task.cancel()
            return False

        sent_any_audio = bool(first_item.get("audio"))

        # Send remaining chunks in order.
        # Usually they will already be waiting in ready_queue.
        for expected_index in range(1, len(segments)):
            item = await ready_queue.get()

            if item.get("index") != expected_index:
                logger.warning(
                    f"{label}: chunk order mismatch. "
                    f"expected={expected_index}, got={item.get('index')}"
                )

            ok = await send_chunk(item)

            if not ok:
                if producer_task:
                    producer_task.cancel()
                return False

            if item.get("audio"):
                sent_any_audio = True

        if producer_task:
            await producer_task

        await websocket.send_json({
            "status": "audio_stream_end"
        })

        total_ms = (time.perf_counter() - stream_start) * 1000
        logger.info(f"{label}: stream complete in {total_ms:.0f}ms")

        if not sent_any_audio:
            await websocket.send_json({
                "status": "error",
                "message": "TTS produced no audio"
            })
            return False

        return True

    except asyncio.TimeoutError:
        logger.error(f"{label}: first TTS chunk timed out")
        await websocket.send_json({
            "status": "error",
            "message": "TTS timeout"
        })

        if producer_task:
            producer_task.cancel()

        return False

    except Exception as e:
        logger.error(f"{label}: streamed TTS error: {e}", exc_info=True)

        await websocket.send_json({
            "status": "error",
            "message": "TTS stream error"
        })

        if producer_task:
            producer_task.cancel()

        return False

async def send_direct_voice_response(websocket: WebSocket,
                                     response_text: str):
    """
    Streamed TTS response.
    Used for smart-home commands that skip the LLM.
    """
    await websocket.send_json({
        "status":   "processing",
        "stage":    "speaking",
        "response": response_text
    })

    await send_streamed_tts_response(
        websocket,
        response_text,
        label="direct_tts_stream"
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
        "status":     "processing",
        "stage":      "command",
        "transcript": transcript,
        "response":   result.response
    })

    await send_direct_voice_response(websocket, result.response)
    return True

async def run_pipeline(websocket: WebSocket,
                       client_id: int,
                       transcript: str):
    """
    LLM → TTS → send audio.
    Called from both the audio path and text_inject path.
    Raises asyncio.TimeoutError on slow LLM/TTS so the
    caller can handle it cleanly.
    """
    loop = asyncio.get_running_loop()
    pipeline_start = time.perf_counter()

    # Recall relevant memories
    mem_start = time.perf_counter()

    memories = memory.recall_memory(transcript)

    mem_ms = (time.perf_counter() - mem_start) * 1000
    logger.info(f"Timing: memory={mem_ms:.0f}ms")

    if memories:
        connections[client_id].append({
            "role":    "system",
            "content": "Relevant context: " + " | ".join(memories)
        })

    # ── LLM ───────────────────────────────────────
    history = connections[client_id]
    try:
        llm_start = time.perf_counter()

        response = await asyncio.wait_for(
            loop.run_in_executor(None, llm.chat, transcript, history),
            timeout=60.0
        )

        response = clean_spoken_response(response)

        llm_ms = (time.perf_counter() - llm_start) * 1000
        logger.info(f"Timing: llm={llm_ms:.0f}ms")
        logger.info(f"LLM cleaned response: '{response}'")

    except asyncio.TimeoutError:
        logger.error("LLM timed out")
        await websocket.send_json({
            "status":  "error",
            "message": "LLM timeout — please try again"
        })
        return

    # Update conversation history
    connections[client_id].append(
        {"role": "user",      "content": transcript})
    connections[client_id].append(
        {"role": "assistant", "content": response})
    if len(connections[client_id]) > 20:
        connections[client_id] = connections[client_id][-20:]

    memory.log_conversation("user",      transcript)
    memory.log_conversation("assistant", response)

    await websocket.send_json({
        "status":   "processing",
        "stage":    "speaking",
        "response": response
    })

    # ── TTS ───────────────────────────────────────
    try:
        
        await send_streamed_tts_response(
            websocket,
            response,
            label="llm_tts_stream"
        )

        total_ms = (time.perf_counter() - pipeline_start) * 1000
        logger.info(f"Timing: pipeline_total={total_ms:.0f}ms")
    
    except asyncio.TimeoutError:
        logger.error("TTS timed out")
        await websocket.send_json({
            "status":  "error",
            "message": "TTS timeout — please try again"
        })
        return


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
                        test_response = ("ZYRA is online and connected. "
                                         "The pipeline is working.")
                        loop = asyncio.get_running_loop()

                        await websocket.send_json({
                            "status":   "processing",
                            "stage":    "speaking",
                            "response": test_response
                        })

                        try:
                            audio_response = await asyncio.wait_for(
                                loop.run_in_executor(
                                    None, tts.synthesize, test_response),
                                timeout=120.0
                            )
                        except asyncio.TimeoutError:
                            logger.error("TTS timed out on test query")
                            continue

                        if audio_response:
                            await websocket.send_json({
                                "status":      "audio_incoming",
                                "audio_bytes": len(audio_response),
                                "sample_rate": tts.sample_rate
                            })
                            await websocket.send_bytes(audio_response)
                            logger.info("Test audio sent")
                        else:
                            logger.error("TTS failed for test query")

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
    return {
        "status": "online",
        "model":  llm.model,
        "memory": "connected",
        "smart_home": {
            "configured_urls": smart_home.base_urls,
            "active_url": smart_home.active_base_url,
        }
    }


# ── Run server ────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"ZYRA server starting on ws://{HOST}:{PORT}")
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        reload=False,
        ws_ping_interval=60,
        ws_ping_timeout=120
    )
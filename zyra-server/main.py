import asyncio
import logging
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from config import HOST, PORT
from stt import STTEngine
from llm import LLMEngine
from tts import TTSEngine
from memory import MemoryEngine

import sys
import os

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
tts    = TTSEngine()
memory = MemoryEngine()
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

    # Recall relevant memories
    memories = memory.recall_memory(transcript)
    if memories:
        connections[client_id].append({
            "role":    "system",
            "content": "Relevant context: " + " | ".join(memories)
        })

    # ── LLM ───────────────────────────────────────
    history = connections[client_id]
    try:
        response = await asyncio.wait_for(
            loop.run_in_executor(None, llm.chat, transcript, history),
            timeout=60.0
        )
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
        audio_response = await asyncio.wait_for(
            loop.run_in_executor(None, tts.synthesize, response),
            timeout=30.0
        )
    except asyncio.TimeoutError:
        logger.error("TTS timed out")
        await websocket.send_json({
            "status":  "error",
            "message": "TTS timeout"
        })
        return

    if audio_response:
        await websocket.send_json({
            "status":      "audio_incoming",
            "audio_bytes": len(audio_response),
            "sample_rate": tts.sample_rate
        })
        await websocket.send_bytes(audio_response)
        logger.info(f"Audio sent — {len(audio_response)} bytes")
    else:
        await websocket.send_json({
            "status":  "error",
            "message": "TTS produced no audio"
        })


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
                    transcript = await asyncio.wait_for(
                        loop.run_in_executor(
                            None, stt.transcribe, audio_bytes),
                        timeout=30.0
                    )
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
                                timeout=30.0
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
        "memory": "connected"
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
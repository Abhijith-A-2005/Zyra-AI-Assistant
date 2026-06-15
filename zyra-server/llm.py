import logging
import requests
import time
import threading

from config import OLLAMA_URL, OLLAMA_MODEL, SYSTEM_PROMPT

logger = logging.getLogger(__name__)


LLM_KEEP_ALIVE = -1

LLM_OPTIONS = {
    "temperature": 0.3, # 0.0 = deterministic, 1.0 = creative
    "top_p":       0.75, # 0.0-1.0, lower = more focused on high-prob tokens
    "num_predict": 96,   # max tokens to generate (roughly 1 token = 0.75 words)
    "num_gpu":     99,  # use all available GPU memory for faster generation
    "num_thread":  6,  # use multiple CPU threads to speed up generation (if GPU is not maxed out)  
}

LLM_WARMUP_OPTIONS = {
    "temperature": 0.0,
    "top_p":       0.5,
    "num_predict": 4,
    "num_gpu":     20,
    "num_thread":  6,
}

LLM_KEEPWARM_INTERVAL_SEC = 300


class LLMEngine:
    def __init__(self):
        self.chat_url = f"{OLLAMA_URL}/api/chat"
        self.generate_url = f"{OLLAMA_URL}/api/generate"
        self.model = OLLAMA_MODEL
        self._request_lock = threading.Lock()

        logger.info(f"LLM engine starting — model: {self.model}")

        self._warmup()

        self._start_keepwarm_thread()

        logger.info(f"LLM engine ready — model hot: {self.model}")

    def _load_model_only(self):
        """
        Loads the model into Ollama memory without doing a real user response.
        """
        start = time.perf_counter()

        response = requests.post(
            self.generate_url,
            json={
                "model": self.model,
                "prompt": "",
                "stream": False,
                "keep_alive": LLM_KEEP_ALIVE,
                "options": {
                    "num_gpu": LLM_OPTIONS["num_gpu"],
                    "num_thread": LLM_OPTIONS["num_thread"],
                },
            },
            timeout=120,
        )

        response.raise_for_status()

        result = response.json()

        load_s = result.get("load_duration", 0) / 1e9
        total_s = (time.perf_counter() - start)

        logger.info(
            f"LLM load-only warmup complete: "
            f"load={load_s:.2f}s total={total_s:.2f}s"
        )

    def _warm_chat_path(self):
        """
        Warms the exact /api/chat path used during real conversations.
        This also primes the system prompt path.
        """
        start = time.perf_counter()

        response = requests.post(
            self.chat_url,
            json={
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": "Say ready.",
                    },
                ],
                "stream": False,
                "keep_alive": LLM_KEEP_ALIVE,
                "options": LLM_OPTIONS,
            },
            timeout=120,
        )

        response.raise_for_status()

        result = response.json()

        load_s = result.get("load_duration", 0) / 1e9
        prompt_s = result.get("prompt_eval_duration", 0) / 1e9
        gen_s = result.get("eval_duration", 0) / 1e9
        total_s = (time.perf_counter() - start)

        content = result.get("message", {}).get("content", "").strip()

        logger.info(
            f"LLM chat warmup complete: "
            f"load={load_s:.2f}s prompt={prompt_s:.2f}s "
            f"gen={gen_s:.2f}s total={total_s:.2f}s "
            f"response='{content}'"
        )

    def _warmup(self):
        """
        Full startup warmup.
        The server startup may take a little longer, but the first user
        command will not pay the LLM cold-start cost.
        """
        try:
            logger.info("Preloading LLM into Ollama memory...")

            with self._request_lock:
                self._warm_chat_path()

            logger.info("LLM warmup finished. First command should be hot.")

        except Exception as e:
            logger.error(f"LLM warmup failed: {e}", exc_info=True)

    def _start_keepwarm_thread(self):
        """
        Keeps the model hot during long idle periods.
        If the user does not talk for a while, this prevents Ollama cold-load
        on the next command.
        """
        thread = threading.Thread(
            target=self._keepwarm_loop,
            name="zyra-llm-keepwarm",
            daemon=True,
        )
        thread.start()

    def _keepwarm_loop(self):
        while True:
            time.sleep(LLM_KEEPWARM_INTERVAL_SEC)

            acquired = self._request_lock.acquire(blocking=False)

            if not acquired:
                logger.info("Skipping LLM keepwarm ping; LLM is busy")
                continue

            try:
                logger.info("Running LLM keepwarm ping...")
                self._warm_chat_path()
            except Exception as e:
                logger.warning(f"LLM keepwarm ping failed: {e}")
            finally:
                self._request_lock.release()

    def chat(self, user_message: str, conversation_history: list) -> str:
        try:
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            messages.extend(conversation_history[-10:])
            messages.append({"role": "user", "content": user_message})

            payload = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "keep_alive": LLM_KEEP_ALIVE,
                "options": LLM_OPTIONS,
            }

            with self._request_lock:
                response = requests.post(
                    self.chat_url,
                    json=payload,
                    timeout=90,
                )

            response.raise_for_status()

            result = response.json()
            content = result["message"]["content"].strip()

            load_s = result.get("load_duration", 0) / 1e9
            prompt_s = result.get("prompt_eval_duration", 0) / 1e9
            gen_s = result.get("eval_duration", 0) / 1e9
            tps = result.get("eval_count", 0) / max(gen_s, 0.001)

            logger.info(
                f"LLM: load={load_s:.2f}s  prompt={prompt_s:.2f}s  "
                f"gen={gen_s:.2f}s  {tps:.1f}tok/s"
            )

            if load_s > 1.0:
                logger.warning(
                    f"LLM was cold during real request: load={load_s:.2f}s"
                )

            logger.info(f"LLM response: '{content}'")

            return content

        except requests.exceptions.Timeout:
            logger.error("LLM timeout")
            return "I am taking too long to think. Please try again."

        except Exception as e:
            logger.error(f"LLM error: {e}", exc_info=True)
            return "I encountered an error. Please try again."
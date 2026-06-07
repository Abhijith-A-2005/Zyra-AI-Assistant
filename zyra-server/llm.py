import requests
from config import OLLAMA_URL, OLLAMA_MODEL, SYSTEM_PROMPT
import logging

logger = logging.getLogger(__name__)


class LLMEngine:
    def __init__(self):
        self.url   = f"{OLLAMA_URL}/api/chat"
        self.model = OLLAMA_MODEL
        logger.info(f"LLM engine ready — model: {self.model}")
        self._warmup()

    def _warmup(self):
        """Pre-load model into GPU and keep it resident permanently."""
        try:
            logger.info("Loading LLM into GPU memory...")
            requests.post(
                self.url,
                json={
                    "model":      self.model,
                    "messages":   [{"role": "user", "content": "hi"}],
                    "stream":     False,
                    "keep_alive": -1,
                    "options":    {"num_predict": 1}
                },
                timeout=60
            )
            logger.info("LLM loaded into GPU — will stay resident")
        except Exception as e:
            logger.warning(f"LLM warmup failed: {e}")

    def chat(self, user_message: str,
             conversation_history: list) -> str:
        try:
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            messages.extend(conversation_history[-10:])
            messages.append({"role": "user", "content": user_message})

            payload = {
                "model":      self.model,
                "messages":   messages,
                "stream":     False,
                "keep_alive": -1,
                "options": {
                    "temperature": 0.6, # 0.0 = deterministic, 1.0 = creative
                    "top_p":       0.85, # 0.0-1.0, lower = more focused on high-prob tokens
                    "num_predict": 50,   # max tokens to generate (roughly 1 token = 0.75 words)
                    "num_gpu":     99,  # use all available GPU memory for faster generation
                    "num_thread":  6,  # use multiple CPU threads to speed up generation (if GPU is not maxed out)  
                }
            }

            response = requests.post(
                self.url,
                json=payload,
                timeout=60
            )
            response.raise_for_status()

            result  = response.json()
            content = result["message"]["content"].strip()

            load_s   = result.get("load_duration",        0) / 1e9
            prompt_s = result.get("prompt_eval_duration", 0) / 1e9
            gen_s    = result.get("eval_duration",        0) / 1e9
            tps      = result.get("eval_count", 0) / max(gen_s, 0.001)
            logger.info(
                f"LLM: load={load_s:.2f}s  prompt={prompt_s:.2f}s  "
                f"gen={gen_s:.2f}s  {tps:.1f}tok/s"
            )
            logger.info(f"LLM response: '{content}'")
            return content

        except requests.exceptions.Timeout:
            logger.error("LLM timeout")
            return "I am taking too long to think. Please try again."

        except Exception as e:
            logger.error(f"LLM error: {e}")
            return "I encountered an error. Please try again."
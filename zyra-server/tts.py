import logging
import io
import wave

logger = logging.getLogger(__name__)

MODEL_PATH = r"C:\zyra-server\models\en_US-lessac-high.onnx"


class TTSEngine:
    def __init__(self):
        logger.info("Loading Piper voice model into memory...")
        from piper.voice import PiperVoice
        self.voice = PiperVoice.load(MODEL_PATH)
        self.sample_rate = self.voice.config.sample_rate
        logger.info(f"TTS engine ready — sample rate: {self.sample_rate}Hz")

    def synthesize(self, text: str) -> bytes:
        """
        Returns raw 16-bit PCM bytes at self.sample_rate Hz, mono.
        Uses synthesize_wav() which writes a proper WAV into a BytesIO buffer,
        then strips the header so the ESP32 gets raw PCM.
        """
        try:
            buf = io.BytesIO()

            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)          # 16-bit
                wf.setframerate(self.sample_rate)
                self.voice.synthesize_wav(text, wf)

            # Strip WAV header — return raw PCM only
            buf.seek(0)
            with wave.open(buf, "rb") as wf:
                raw_pcm = wf.readframes(wf.getnframes())

            logger.info(
                f"TTS synthesized {len(raw_pcm)} bytes "
                f"for: '{text[:60]}'"
            )
            return raw_pcm

        except Exception as e:
            logger.error(f"TTS error: {e}", exc_info=True)
            return b""
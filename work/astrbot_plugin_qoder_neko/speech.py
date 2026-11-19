"""Local recognition of the WAV delivered by WeChat Native."""

import asyncio
import re
import threading
import wave
from pathlib import Path


class LocalSpeech:
    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self.recognizer = None
        self.lock = threading.Lock()

    async def transcribe(self, path: str) -> str:
        return await asyncio.to_thread(self._decode, path)

    def _decode(self, path: str) -> str:
        import numpy as np
        import sherpa_onnx

        with wave.open(path, "rb") as audio:
            rate, channels, frames = audio.getframerate(), audio.getnchannels(), audio.getnframes()
            if audio.getsampwidth() != 2 or channels not in (1, 2) or not 8000 <= rate <= 48000:
                raise ValueError("Unsupported WeChat WAV format")
            if not 0 < frames <= rate * 70:
                raise ValueError("WeChat voice duration is outside the supported range")
            samples = np.frombuffer(audio.readframes(frames), dtype="<i2").astype(np.float32)
        if channels == 2:
            samples = samples.reshape(-1, channels).mean(axis=1)
        samples /= 32768

        with self.lock:
            if self.recognizer is None:
                model = self.model_dir / "model.int8.onnx"
                tokens = self.model_dir / "tokens.txt"
                if not model.is_file() or not tokens.is_file():
                    raise FileNotFoundError("SenseVoice model files are missing")
                self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=str(model), tokens=str(tokens), num_threads=2, language="auto", use_itn=True,
                )
            stream = self.recognizer.create_stream()
            stream.accept_waveform(rate, samples)
            self.recognizer.decode_stream(stream)
            text = re.sub(r"<\|[^<>|]+\|>", "", stream.result.text).strip()
        return text[:2000] if re.search(r"\w", text) else ""

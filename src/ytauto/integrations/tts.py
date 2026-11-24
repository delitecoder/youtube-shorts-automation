"""Text-to-speech with a fallback chain. One video always uses ONE voice.

Providers (licence / status verified Nov 2026, see docs/costs.md):
  google  – Cloud Text-to-Speech (Chirp 3 HD default). Official API; free tier then paid.
            Monthly character budget enforced locally before every call.
  kokoro  – Kokoro-82M (Apache-2.0 weights) via kokoro-onnx, fully offline on CPU (~real-time
            on 4 vCPU). Model files fetched by scripts/fetch_kokoro.sh and verified by SHA-256.
  edge    – Microsoft Edge "read aloud" voices via the unofficial edge-tts client. NOT an
            official API: Microsoft has blocked it before and has not granted commercial use.
            Disabled unless ALLOW_EDGE_TTS=true (owner accepts the terms risk).
  piper   – Piper offline TTS; only with a voice model whose licence permits commercial use.
  tone    – tests only.

`synthesize_all` tries the primary provider for every scene; if any scene fails (error,
budget, missing model) it discards that provider's clips and re-voices the whole video with the
next provider, so a video never mixes voices.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path

from ..config import RUNTIME_DIR
from ..state import utcnow

MODELS_DIR = RUNTIME_DIR / "models"


class BudgetExceeded(RuntimeError):
    pass


class ProviderUnavailable(RuntimeError):
    pass


class TTSBudget:
    def __init__(self, store, monthly_limit: int):
        self.store, self.limit = store, monthly_limit

    @property
    def key(self) -> str:
        return f"tts_chars_{utcnow():%Y-%m}"

    def used(self) -> int:
        return int(self.store.get_config(self.key, "0") or 0)

    def check(self, n: int) -> None:
        used = self.used()
        if used + n > self.limit:
            raise BudgetExceeded(f"TTS budget: {used}+{n} > {self.limit} chars this month")

    def reserve(self, n: int) -> None:
        self.check(n)
        self.store.set_config(self.key, str(self.used() + n))


class GoogleTTS:
    name = "google"
    ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"

    def __init__(self, voice: str, language: str, budget: TTSBudget, speaking_rate: float = 1.0):
        self.voice, self.language, self.budget, self.rate = voice, language, budget, speaking_rate

    def preflight(self, texts: list[str]) -> None:
        self.budget.check(sum(len(t) for t in texts))   # whole video fits, or don't start

    def synthesize(self, text: str, out: Path) -> Path:
        import requests

        if len(text.encode()) > 4500:
            raise ValueError("scene narration too long for a single request")
        self.budget.reserve(len(text))
        key = os.environ.get("GOOGLE_TTS_API_KEY", "injected-by-proxy")
        body = {"input": {"text": text},
                "voice": {"languageCode": self.language, "name": self.voice},
                "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 48000}}
        if "Chirp3" not in self.voice:
            body["audioConfig"]["speakingRate"] = self.rate
        r = requests.post(self.ENDPOINT, json=body, headers={"X-Goog-Api-Key": key}, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"Google TTS HTTP {r.status_code}: {r.text[:300]}")
        out.write_bytes(base64.b64decode(r.json()["audioContent"]))
        return out


class KokoroTTS:
    name = "kokoro"
    MODEL, VOICES = "kokoro-v1.0.int8.onnx", "voices-v1.0.bin"

    def __init__(self, voice: str = "am_michael", speed: float = 1.0, models_dir: Path | None = None):
        d = Path(os.environ.get("KOKORO_MODELS_DIR") or models_dir or MODELS_DIR)
        self.model, self.voices = d / self.MODEL, d / self.VOICES
        if not (self.model.exists() and self.voices.exists()):
            raise ProviderUnavailable(f"Kokoro model files missing in {d} (run scripts/fetch_kokoro.sh)")
        try:
            from kokoro_onnx import Kokoro  # noqa: F401
        except ImportError as e:
            raise ProviderUnavailable("kokoro-onnx not installed") from e
        self.voice, self.speed, self._k = voice, speed, None

    def preflight(self, texts):
        pass

    def synthesize(self, text: str, out: Path) -> Path:
        import soundfile as sf
        from kokoro_onnx import Kokoro

        if self._k is None:
            self._k = Kokoro(str(self.model), str(self.voices))
        samples, sr = self._k.create(text, voice=self.voice, speed=self.speed, lang="en-us")
        sf.write(str(out), samples, sr)
        return out


class EdgeTTS:
    name = "edge"

    def __init__(self, voice: str, allowed: bool):
        if not allowed:
            raise ProviderUnavailable("Edge TTS disabled (set ALLOW_EDGE_TTS=true to accept its terms risk)")
        if not shutil.which("edge-tts"):
            raise ProviderUnavailable("edge-tts not installed")
        self.voice = voice

    def preflight(self, texts):
        pass

    def synthesize(self, text: str, out: Path) -> Path:
        mp3 = out.with_suffix(".mp3")
        subprocess.run(["edge-tts", "--voice", self.voice, "--text", text, "--write-media", str(mp3)],
                       check=True, capture_output=True, timeout=120)
        if mp3.stat().st_size == 0:
            raise RuntimeError("edge-tts returned empty audio")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), str(out)], check=True)
        return out


class PiperTTS:
    name = "piper"

    def __init__(self, model_path: str):
        if not shutil.which("piper"):
            raise ProviderUnavailable("piper binary not installed")
        if not model_path or not Path(model_path).exists():
            raise ProviderUnavailable(f"piper model not found: {model_path or '(PIPER_MODEL unset)'}")
        self.model = model_path

    def preflight(self, texts):
        pass

    def synthesize(self, text: str, out: Path) -> Path:
        subprocess.run(["piper", "--model", self.model, "--output_file", str(out)],
                       input=text.encode(), check=True, capture_output=True)
        return out


class ToneTTS:
    """Test stand-in: a soft tone whose length follows a ~2.6 words/second speaking pace."""
    name = "tone"

    def __init__(self, words_per_second: float = 2.6):
        self.wps = words_per_second

    def preflight(self, texts):
        pass

    def synthesize(self, text: str, out: Path) -> Path:
        secs = max(0.8, len(text.split()) / self.wps)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency=220:duration={secs:.2f}:sample_rate=48000",
                        "-af", "volume=0.2", "-ac", "1", str(out)], check=True)
        return out


def build_provider(name: str, settings, store):
    if name == "google":
        return GoogleTTS(settings.tts_voice, settings.tts_language,
                         TTSBudget(store, settings.tts_monthly_char_budget))
    if name == "kokoro":
        return KokoroTTS(settings.kokoro_voice)
    if name == "edge":
        return EdgeTTS(settings.edge_voice, settings.allow_edge_tts)
    if name == "piper":
        return PiperTTS(os.environ.get("PIPER_MODEL", ""))
    if name == "tone":
        if not settings.dry_run:
            raise ProviderUnavailable("tone TTS is for tests only; refusing outside DRY_RUN")
        return ToneTTS()
    raise ValueError(f"unknown TTS provider {name}")


def synthesize_all(texts: list[str], outdir: Path, settings, store, log=print) -> tuple[list[Path], str, list[str]]:
    """Voice every text with the first provider that succeeds for ALL of them.
    Returns (clip paths, provider name, list of fallback reasons)."""
    order = [settings.tts_provider] + [p for p in settings.tts_fallbacks if p != settings.tts_provider]
    reasons: list[str] = []
    for name in order:
        try:
            prov = build_provider(name, settings, store)
            prov.preflight(texts)
            clips = [prov.synthesize(t, outdir / f"{name}_scene{i + 1}.wav") for i, t in enumerate(texts)]
            return clips, name, reasons
        except (ProviderUnavailable, BudgetExceeded, RuntimeError, OSError, ValueError,
                subprocess.SubprocessError) as e:
            reasons.append(f"{name}: {str(e)[:200]}")
            log(f"TTS provider {name} failed, trying next: {str(e)[:200]}")
    raise RuntimeError("all TTS providers failed: " + " | ".join(reasons))


def make_provider(settings, store):  # kept for compatibility
    return build_provider(settings.tts_provider, settings, store)

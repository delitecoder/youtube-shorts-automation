"""Image understanding with a fallback chain — no Claude tokens spent on images.

Order (VISION_PROVIDERS, default "omnivision,smolvlm,google,clip,metadata"):
  omnivision – NexaAI OmniVLM-968M (Apache-2.0, GGUF, CPU) via VISION_CMD; caption + flags.
  smolvlm    – HuggingFaceTB SmolVLM-500M-Instruct (Apache-2.0) via transformers, CPU (~20 s per
               image on 4 vCPU, measured): a DETAILED 2–3 sentence description of what is in the
               photo (objects, setting, colours, visible text) + yes/no flags. Set VLM_MODEL to
               SmolVLM-256M-Instruct for speed or a larger VLM for quality.
  google     – Google Cloud Vision API (labels, logos, faces, text). First 1,000 units per feature
               per month are free, then paid; local budget VISION_MONTHLY_UNIT_BUDGET (default 800
               images) stops before the free tier is used up. API key: network secret / env.
  clip       – OpenCLIP ViT-B-32 (MIT code, LAION weights) zero-shot relevance + watermark/logo
               checks, plus YOLOv8n (Ultralytics, AGPL-3.0 — used internally only, never
               distributed) to detect prominent people. CPU: ~0.1–0.7 s per image.
  metadata   – nothing is seen; result flagged visual_unverified for the human approver.
A provider that is unavailable or errors is skipped; the first complete answer wins.
Every result is a small dict: caption, relevance (0–1, optional), text/watermark/logo/face flags.
"""
from __future__ import annotations

import base64
import os
import re
import shlex
import subprocess
from pathlib import Path

from studio.core.state import utcnow

PROMPT = ("Answer in exactly this format. CAPTION: <one short sentence>. TEXT: yes/no. "
          "WATERMARK: yes/no. LOGO: yes/no. FACE: yes/no.")
FLAG_RE = {k: re.compile(rf"{k.upper()}\s*:\s*(yes|no)", re.I) for k in ("text", "watermark", "logo", "face")}
CAP_RE = re.compile(r"CAPTION\s*:\s*(.+?)(?:\.\s*(?:TEXT|WATERMARK|LOGO|FACE)\s*:|$)", re.I | re.S)


class Unavailable(RuntimeError):
    pass


def parse(output: str) -> dict:
    res: dict = {"caption": ""}
    m = CAP_RE.search(output)
    res["caption"] = (m.group(1) if m else output.strip().splitlines()[0] if output.strip() else "").strip()[:200]
    for k, rx in FLAG_RE.items():
        f = rx.search(output)
        res[k] = bool(f and f.group(1).lower() == "yes")
    res["parsed"] = bool(m) and all(FLAG_RE[k].search(output) for k in ("watermark", "logo", "face"))
    return res


# ------------------------------------------------------------------ providers
class CommandDescriber:
    """OmniVision (or any VLM CLI) through a command template, run without a shell."""
    name = "omnivision"

    def __init__(self, template: str, timeout: int = 180):
        if not template:
            raise Unavailable("VISION_CMD not set")
        self.template, self.timeout = template, timeout

    def describe(self, image: Path, query: str = "") -> dict:
        args = [a.replace("{image}", str(image)).replace("{prompt}", PROMPT) for a in shlex.split(self.template)]
        r = subprocess.run(args, capture_output=True, text=True, timeout=self.timeout)
        if r.returncode != 0:
            raise RuntimeError(f"vision command failed: {r.stderr[-200:]}")
        out = parse(r.stdout)
        if not out.pop("parsed"):
            raise RuntimeError("vision output not in expected format")
        return out


class GoogleVision:
    name = "google"
    ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"

    def __init__(self, store=None, monthly_limit: int = 800):
        if os.environ.get("GOOGLE_VISION_ENABLED", "").lower() != "true":
            raise Unavailable("GOOGLE_VISION_ENABLED is not true (needs billing account)")
        self.store, self.limit = store, monthly_limit

    def _reserve(self):
        if self.store is None:
            return
        key = f"vision_images_{utcnow():%Y-%m}"
        used = int(self.store.get_config(key, "0") or 0)
        if used + 1 > self.limit:
            raise Unavailable(f"Vision budget reached ({used}/{self.limit} images this month)")
        self.store.set_config(key, str(used + 1))

    def describe(self, image: Path, query: str = "") -> dict:
        import requests
        from PIL import Image

        self._reserve()
        body = {"requests": [{"image": {"content": base64.b64encode(image.read_bytes()).decode()},
                              "features": [{"type": "LABEL_DETECTION", "maxResults": 6},
                                           {"type": "LOGO_DETECTION", "maxResults": 3},
                                           {"type": "FACE_DETECTION", "maxResults": 3},
                                           {"type": "TEXT_DETECTION", "maxResults": 5}]}]}
        key = os.environ.get("GOOGLE_VISION_API_KEY", "injected-by-proxy")
        r = requests.post(self.ENDPOINT, json=body, headers={"X-Goog-Api-Key": key}, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"Vision HTTP {r.status_code}: {r.text[:200]}")
        a = r.json()["responses"][0]
        labels = [x["description"] for x in a.get("labelAnnotations", [])]
        texts = a.get("textAnnotations", [])[1:]
        with Image.open(image) as im:
            w, h = im.size
        wm = any(_corner_box(t.get("boundingPoly", {}).get("vertices", []), w, h) for t in texts)
        return {"caption": ", ".join(labels), "text": bool(texts), "watermark": wm,
                "logo": bool(a.get("logoAnnotations")), "face": bool(a.get("faceAnnotations"))}


def _corner_box(v, w, h) -> bool:
    """Small text near an edge/corner looks like a watermark or credit stamp."""
    if not v:
        return False
    xs, ys = [p.get("x", 0) for p in v], [p.get("y", 0) for p in v]
    small = (max(xs) - min(xs)) * (max(ys) - min(ys)) < 0.05 * w * h
    edge = min(xs) < 0.15 * w or max(xs) > 0.85 * w or min(ys) < 0.15 * h or max(ys) > 0.85 * h
    return small and edge


class ClipYolo:
    """OpenCLIP zero-shot relevance + watermark/logo; YOLOv8n for prominent people."""
    name = "clip+yolo"
    _clip = None
    _yolo = None

    def __init__(self):
        try:
            import open_clip  # noqa: F401
            import torch  # noqa: F401
        except ImportError as e:
            raise Unavailable("open_clip/torch not installed (requirements-vision.txt)") from e

    def _models(self):
        import open_clip
        if ClipYolo._clip is None:
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            m, _, pre = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")
            ClipYolo._clip = (m.eval(), pre, open_clip.get_tokenizer("ViT-B-32"))
        if ClipYolo._yolo is None:
            try:
                from ultralytics import YOLO
                ClipYolo._yolo = YOLO(os.environ.get("YOLO_WEIGHTS", "yolov8n.pt"))
            except Exception:  # noqa: BLE001 - YOLO is optional
                ClipYolo._yolo = False
        return ClipYolo._clip, ClipYolo._yolo

    @staticmethod
    def _pair(sims, i, j) -> float:
        import math
        a, b = math.exp(100 * sims[i]), math.exp(100 * sims[j])
        return a / (a + b)

    def describe(self, image: Path, query: str = "") -> dict:
        import torch
        from PIL import Image

        (model, pre, tok), yolo = self._models()
        prompts = [f"a photo of {query}" if query else "a photo",
                   "a photo with a visible watermark", "a clean photo without a watermark",
                   "a company logo or brand mark", "a photo without any logos",
                   "a close-up photo of a person's face", "a photo with no people"]
        with torch.no_grad():
            img = Image.open(image).convert("RGB")
            f = model.encode_image(pre(img).unsqueeze(0))
            f = f / f.norm(dim=-1, keepdim=True)
            t = model.encode_text(tok(prompts))
            t = t / t.norm(dim=-1, keepdim=True)
            s = (f @ t.T)[0].tolist()
        res = {"caption": f"matches '{query}' (clip {s[0]:.2f})" if query else "",
               "relevance": max(0.0, min(1.0, (s[0] - 0.15) / 0.15)),
               "watermark": self._pair(s, 1, 2) > 0.7, "logo": self._pair(s, 3, 4) > 0.7,
               "face": self._pair(s, 5, 6) > 0.8, "text": False}
        if yolo:
            r = yolo.predict(str(image), verbose=False, conf=0.4, device="cpu")[0]
            H, W = r.orig_shape
            people = [b for b, c in zip(r.boxes.xywh.tolist(), r.boxes.cls.tolist()) if r.names[int(c)] == "person"]
            res["face"] = res["face"] or any(b[2] * b[3] > 0.05 * W * H for b in people)
            objs = sorted({r.names[int(c)] for c in r.boxes.cls.tolist()})
            if objs:
                res["caption"] += f"; objects: {', '.join(objs[:6])}"
        return res


class SmolVLM:
    name = "smolvlm"
    _cache: dict = {}
    DESCRIBE = ("Describe this image in detail in 2-3 sentences: the main subject, important objects, "
                "setting, colours and any visible text.")
    # Small VLMs answer one yes/no question reliably, not a multi-part form (measured).
    QUESTIONS = {
        "watermark": "Is there a watermark or photographer's stamp on this image? Answer yes or no.",
        "logo": "Is there a company or brand logo in this image? Answer yes or no.",
        "face": "Is a person's face clearly visible in this image? Answer yes or no.",
    }

    def __init__(self, model_id: str | None = None):
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except ImportError as e:
            raise Unavailable("transformers/torch not installed (requirements-vision.txt)") from e
        self.model_id = model_id or os.environ.get("VLM_MODEL", "HuggingFaceTB/SmolVLM-500M-Instruct")

    def _load(self):
        if self.model_id not in SmolVLM._cache:
            import torch
            from transformers import AutoModelForImageTextToText, AutoProcessor
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            proc = AutoProcessor.from_pretrained(self.model_id)
            model = AutoModelForImageTextToText.from_pretrained(self.model_id, dtype=torch.float32).eval()
            SmolVLM._cache[self.model_id] = (proc, model)
        return SmolVLM._cache[self.model_id]

    def _ask(self, img, question: str, max_new_tokens: int) -> str:
        import torch
        proc, model = self._load()
        msgs = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": question}]}]
        inp = proc(text=proc.apply_chat_template(msgs, add_generation_prompt=True), images=[img],
                   return_tensors="pt")
        with torch.no_grad():
            out = model.generate(**inp, max_new_tokens=max_new_tokens, do_sample=False)
        return proc.batch_decode(out[:, inp["input_ids"].shape[1]:], skip_special_tokens=True)[0].strip()

    def describe(self, image: Path, query: str = "") -> dict:
        from PIL import Image
        img = Image.open(image).convert("RGB")
        caption = " ".join(self._ask(img, self.DESCRIBE, 110).split())
        res = {"caption": caption[:600], "text": None}
        for k, q in self.QUESTIONS.items():
            a = self._ask(img, q, 3).strip().lower()
            if not a.startswith(("yes", "no")):
                raise RuntimeError(f"VLM gave no yes/no answer for {k}: {a[:30]!r}")
            res[k] = a.startswith("yes")
        return res


class GroundingDino:
    """Open-vocabulary detector (IDEA-Research grounding-dino-tiny, Apache-2.0): boxes for any
    named object. Used for framing (where to zoom) and as a high-confidence logo/watermark check.
    Low scores give false positives (measured: fibre connectors labelled "watermark" at 0.25), so
    only scores >= FLAG_MIN count as flags."""
    FLAG_MIN = 0.40
    _cache: dict = {}

    def __init__(self, model_id: str | None = None):
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except ImportError as e:
            raise Unavailable("transformers/torch not installed") from e
        self.model_id = model_id or os.environ.get("DETECTOR_MODEL", "IDEA-Research/grounding-dino-tiny")

    def _load(self):
        if self.model_id not in GroundingDino._cache:
            from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            GroundingDino._cache[self.model_id] = (
                AutoProcessor.from_pretrained(self.model_id),
                AutoModelForZeroShotObjectDetection.from_pretrained(self.model_id).eval())
        return GroundingDino._cache[self.model_id]

    def detect(self, image: Path, query: str) -> dict:
        """Returns {"focus": [x0,y0,x1,y1] normalised 0–1 or None, "logo": bool, "watermark": bool,
        "boxes": [[label, score, x0, y0, x1, y1], …] (normalised, top 5)}."""
        import torch
        from PIL import Image
        proc, model = self._load()
        img = Image.open(image).convert("RGB")
        W, H = img.size
        subjects = [w for w in re.findall(r"[a-z]+", query.lower()) if w not in _STOP and len(w) > 2][:4]
        text = ". ".join(subjects + ["logo", "watermark"]) + "."
        inp = proc(images=img, text=text, return_tensors="pt")
        with torch.no_grad():
            out = model(**inp)
        r = proc.post_process_grounded_object_detection(out, inp.input_ids, threshold=0.25, text_threshold=0.2,
                                                        target_sizes=[(H, W)])[0]
        labels = r.get("text_labels", r["labels"])
        boxes = sorted(([str(l), round(float(sc), 2), *[round(v, 3) for v in
                         (b[0] / W, b[1] / H, b[2] / W, b[3] / H)]]
                        for b, sc, l in zip(r["boxes"].tolist(), r["scores"].tolist(), labels)),
                       key=lambda x: -x[1])
        area = lambda b: (b[4] - b[2]) * (b[5] - b[3])  # noqa: E731
        # a box covering most of the frame says nothing about where to look
        subj = [b for b in boxes if any(w in b[0] for w in subjects) and 0.02 < area(b) < 0.75]
        flag = lambda word: any(word in b[0] and b[1] >= self.FLAG_MIN for b in boxes)  # noqa: E731
        return {"focus": subj[0][2:] if subj else None, "logo": flag("logo"),
                "watermark": flag("watermark"), "boxes": boxes[:5]}


def make_detector():
    if "detector" not in os.environ.get("VISION_PROVIDERS", "omnivision,smolvlm,google,clip,detector,metadata"):
        return None
    try:
        return GroundingDino()
    except Unavailable:
        return None


class MetadataOnly:
    name = "metadata"

    def describe(self, image: Path, query: str = "") -> dict:
        return {"caption": "", "visual_unverified": True}


# ------------------------------------------------------------------ chain
class ChainDescriber:
    def __init__(self, providers: list):
        self.providers = providers
        self.name = "+".join(p.name for p in providers) or "none"

    def describe(self, image: Path, query: str = "") -> dict:
        errors = []
        for p in self.providers:
            try:
                out = p.describe(image, query)
                out["provider"] = p.name
                if errors:
                    out["fallback_reasons"] = errors
                return out
            except (Unavailable, RuntimeError, OSError, subprocess.SubprocessError, ValueError) as e:
                errors.append(f"{p.name}: {str(e)[:120]}")
        return {"caption": "", "visual_unverified": True, "provider": "none", "fallback_reasons": errors}


def make_describer(store=None):
    order = os.environ.get("VISION_PROVIDERS", "omnivision,smolvlm,google,clip,detector,metadata").split(",")
    built = []
    for name in (o.strip() for o in order):
        try:
            if name == "omnivision":
                built.append(CommandDescriber(os.environ.get("VISION_CMD", "").strip()))
            elif name == "smolvlm":
                built.append(SmolVLM())
            elif name == "google":
                built.append(GoogleVision(store, int(os.environ.get("VISION_MONTHLY_UNIT_BUDGET", "800"))))
            elif name == "clip":
                built.append(ClipYolo())
            elif name == "metadata":
                built.append(MetadataOnly())
        except Unavailable:
            continue
    return ChainDescriber(built or [MetadataOnly()])


# ------------------------------------------------------------------ token-cheap summary
_STOP = set("""a an the this that these those is are was were be been being of in on at to for with and or
but from by as it its into over under image photo picture shows showing depicts depicted there
which who whom whose also very likely appears appear seen visible some several many various one two
three each other their them they he she his her you your we our can could may might would should
large small big little main section part top bottom left right side front back depict focusing focus
focused specific central featuring feature setting organized pattern space foreground background end
connected such like grid other each multiple row rows area view using used way""".split())


def summarize(result: dict, max_keywords: int = 6) -> dict:
    """Compress a vision result to ~30–40 tokens for Claude. The full caption stays on disk."""
    words = re.findall(r"[a-zA-Z][a-zA-Z-]{2,}", result.get("caption", "").lower())
    from collections import Counter
    norm = [w[:-1] if w.endswith("s") and not w.endswith("ss") and len(w) > 4 else w for w in words]
    counts = Counter(w for w in norm if w not in _STOP)
    first = {w: i for i, w in reversed(list(enumerate(norm)))}
    kw = sorted(counts, key=lambda w: (-counts[w], first[w]))   # most mentioned, then earliest
    flags = "".join(k[0] for k in ("watermark", "logo", "face") if result.get(k))  # e.g. "lf"
    out = {"kw": kw[:max_keywords], "flags": flags or "-", "by": result.get("provider", "?")}
    if result.get("focus"):
        out["focus"] = [round(v, 2) for v in result["focus"]]
    if result.get("relevance") is not None:
        out["rel"] = round(float(result["relevance"]), 2)
    if result.get("visual_unverified"):
        out["unverified"] = 1
    return out

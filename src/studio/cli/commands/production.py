"""`studio` commands: production."""
from __future__ import annotations

from pathlib import Path
from studio.core.state import State
from studio.core.state import VideoRecord
from studio.core.state import utcnow
from studio.editorial import storyboard as sbmod
from studio.orchestration.context import Ctx
from studio.storage.store import is_long
from studio.storage.store import language_of
from studio.storage.store import log_error
from studio.storage.store import next_video_id
import json
import os
import shutil
import subprocess
import sys

from studio.cli.common import (fit_json, LIBRARY, WORK, load_json, out)


def cmd_validate_storyboard(ctx: Ctx, a) -> int:
    sb = load_json(a.file)
    errs, warns = sbmod.validate_storyboard(sb, require_sources=not a.document_based)
    out({"ok": not errs, "errors": errs, "warnings": warns, "estimate": sbmod.estimate(sb)})
    return 0 if not errs else 2


def cmd_render(ctx: Ctx, a) -> int:
    from studio.audio.tts import synthesize_all
    from studio.video.render import render
    sb = load_json(a.storyboard)
    errs, _ = sbmod.validate_storyboard(sb, require_sources=not a.document_based)
    rec = ctx.store.get(a.video_id)
    wd = WORK / rec.video_id
    for i, s in enumerate(sb["scenes"], 1):
        v = s["visual"]
        if v["type"] == "image" and not (wd / "assets" / Path(v["asset"]).name).exists():
            errs.append(f"scene {i}: image asset {v['asset']} missing (run resolve-images / fetch-asset)")
    if errs:
        out({"ok": False, "errors": errs})
        return 2
    if sb["video_id"] != rec.video_id:
        out({"ok": False, "error": "storyboard video_id mismatch"})
        return 2
    allowed = {State.RESEARCHING, State.QUALITY_CHECK, State.GENERATING}
    if rec.state not in allowed:
        out({"ok": False, "error": f"cannot render in state {rec.state.value}"})
        return 2
    if rec.state != State.GENERATING:
        rec = ctx.store.transition(rec.video_id, rec.state, State.GENERATING, rec.version)
    wd.mkdir(parents=True, exist_ok=True)
    long_ = sbmod.is_long(sb)
    lang = sb.get("language", "en")
    if long_ != is_long(rec.video_id) or lang != language_of(rec.video_id):
        out({"ok": False, "error": "storyboard format/language does not match the video id "
             "(Short en 001–050, ta 051–099; long en 101–150, ta 151–199)"})
        return 2
    full = sbmod.with_outro(sb, ctx.s.outro_for(long_, lang)) if ctx.s.outro_enabled else sb
    texts = [s["narration"] for s in full["scenes"]]
    from studio.audio.tts import for_language
    try:
        clips, provider, fallbacks = synthesize_all(texts, wd, for_language(ctx.s, lang), ctx.store,
                                                    log=lambda m: print(m, file=sys.stderr))
        report = render(full, clips, wd, wd / f"{rec.video_id}.mp4", music=ctx.s.music_enabled,
                        sfx=ctx.s.sfx_enabled, sfx_dir=LIBRARY / "sfx", music_dir=LIBRARY / "music",
                        engine=os.environ.get("RENDER_ENGINE", "auto"))
    except Exception as e:  # noqa: BLE001
        log_error(ctx.store, "render", str(e), rec.video_id)
        out({"ok": False, "error": str(e)[:500], "hint": "fix and re-run render (state stays GENERATING)"})
        return 2
    report |= {"voice": provider, "voice_fallbacks": fallbacks}
    if not report["ok"]:
        from studio.notifications.templates import notify
        a_qc, v_qc = report.get("audio_qc") or {}, report.get("video_qc") or {}
        event = "audio_qc_failed" if a_qc and not a_qc.get("pass") and v_qc.get("pass", True) else "render_failed"
        try:
            notify(ctx.store, event, rec.video_id, items={"video critical": "; ".join(v_qc.get("critical", [])) or "-",
                                                          "audio critical": "; ".join(a_qc.get("critical", [])) or "-"})
        except Exception:  # noqa: BLE001 - a notification never hides the real error
            pass
        out({"ok": False, "qc": report})
        return 2
    rec = ctx.store.transition(rec.video_id, State.GENERATING, State.QUALITY_CHECK, rec.version,
                               duration_s=str(report["duration"]), width="1920" if long_ else "1080",
                               height="1080" if long_ else "1920",
                               hook=sb["hooks"][sb["chosen_hook"]],
                               references=json.dumps(sb.get("references", [])),
                               quality_report=fit_json({"render_qc": report}))
    out({"ok": True, "video": str(wd / f"{rec.video_id}.mp4"), "voice": provider,
         "engine": report.get("engine"), "fallback_reason": report.get("fallback_reason", ""),
         "fallbacks": fallbacks, "qc": {k: report[k] for k in ("ok", "duration", "lufs")},
         "tts_chars": sum(len(t) for t in texts)})
    return 0


def cmd_voice(ctx: Ctx, a) -> int:
    """Create the narration voice on its own (before render). Clips are cached by exact text, so
    `render` re-uses them and only changed scenes ever cost characters again."""
    from studio.audio.tts import TTSBudget, clean_text, synthesize_all
    sb = load_json(a.storyboard)
    rec = ctx.store.get(a.video_id)
    wd = WORK / rec.video_id
    wd.mkdir(parents=True, exist_ok=True)
    from studio.audio.tts import for_language
    long_ = sbmod.is_long(sb)
    lang = sb.get("language", "en")
    if lang != language_of(rec.video_id):
        out({"ok": False, "error": f"storyboard language {lang} does not match {rec.video_id}"})
        return 2
    full = sbmod.with_outro(sb, ctx.s.outro_for(long_, lang)) if ctx.s.outro_enabled else sb
    texts = [s["narration"] for s in full["scenes"]]
    budget = TTSBudget(ctx.store, ctx.s.tts_monthly_char_budget)
    before = budget.used()
    clips, provider, fallbacks = synthesize_all(texts, wd, for_language(ctx.s, lang), ctx.store,
                                                log=lambda m: print(m, file=sys.stderr))
    used = budget.used()
    out({"ok": True, "language": lang, "voice": provider, "fallbacks": fallbacks, "clips": len(clips),
         "video_chars": sum(len(clean_text(t)) for t in texts), "chars_billed_now": used - before,
         "month_used": used, "month_limit": ctx.s.tts_monthly_char_budget,
         "google_free_tier": 1_000_000})
    return 0


def cmd_texts(ctx: Ctx, a) -> int:
    """Export the translatable texts of a storyboard (for the translator agent)."""
    from studio.editorial import texts as tx
    items = tx.export(sbmod.normalize(load_json(a.storyboard)))
    Path(a.out).write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    out({"ok": True, "file": a.out, "items": len(items),
         "chars": sum(len(x["text"]) for x in items)})
    return 0


def cmd_localize(ctx: Ctx, a) -> int:
    """Make the Tamil (or other language) version of a finished English video: a new item with
    its own id, the same visuals/assets/custom scenes, the translated storyboard (and metadata)
    written by the translator agent. Then: voice → render → frames → submit as usual."""
    src = ctx.store.get(a.video_id)
    if language_of(src.video_id) != "en":
        out({"ok": False, "error": "localize starts from the English video"})
        return 2
    if a.texts:                     # preferred: only the texts change, visuals are the English ones
        from studio.editorial import texts as tx
        en_sb = load_json(a.storyboard or (WORK / src.video_id / "storyboard.json"))
        sb, terrs = tx.merge(en_sb, load_json(a.texts), a.lang)
        if terrs:
            out({"ok": False, "errors": terrs[:20]})
            return 2
    else:
        sb = load_json(a.storyboard)
    long_ = is_long(src.video_id)
    if sbmod.is_long(sb) != long_:
        out({"ok": False, "error": "translated storyboard must keep the original format"})
        return 2
    existing = [r for r in ctx.store.all() if (r.notes or "").startswith(f"{a.lang} version of {src.video_id}")]
    if existing:
        rec = existing[0]
    else:
        vid = next_video_id([r.video_id for r in ctx.store.all()], utcnow(), ctx.s.timezone, long=long_, lang=a.lang)
        rec = ctx.store.create(VideoRecord(video_id=vid, source_type="GENERATED",
                                           notes=f"{a.lang} version of {src.video_id}"))
    sb = dict(sb, video_id=rec.video_id, language=a.lang)
    errs, warns = sbmod.validate_storyboard(sb)
    if errs:
        out({"ok": False, "video_id": rec.video_id, "errors": errs, "warnings": warns})
        return 2
    src_wd, wd = WORK / src.video_id, WORK / rec.video_id
    wd.mkdir(parents=True, exist_ok=True)
    for sub_ in ("assets", "scenes"):
        if (src_wd / sub_).exists():
            shutil.copytree(src_wd / sub_, wd / sub_, dirs_exist_ok=True)
    for f in ("image_credits.json", "research.json"):
        if (src_wd / f).exists():
            shutil.copy(src_wd / f, wd / f)
    (wd / "storyboard.json").write_text(json.dumps(sb, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.metadata:
        shutil.copy(a.metadata, wd / "metadata.json")
    if rec.state == State.DISCOVERED:
        rec = ctx.store.transition(rec.video_id, State.DISCOVERED, State.RESEARCHING, rec.version,
                                   references=src.references or json.dumps(sb.get("references", [])))
    out({"ok": True, "video_id": rec.video_id, "language": a.lang, "from": src.video_id, "warnings": warns,
         "storyboard": str(wd / "storyboard.json"),
         "next": f"python -m studio voice --video-id {rec.video_id} --storyboard {wd / 'storyboard.json'}"})
    return 0


def cmd_resolve_images(ctx: Ctx, a) -> int:
    """Find licensed internet images for `image` scenes that have a `query` but no file yet.
    Unresolvable scenes fall back to a title card, so rendering never blocks on images."""
    import requests

    from studio.research.vision import make_describer, make_detector, summarize
    from studio.research import images as im
    sb = load_json(a.storyboard)
    wd = WORK / sb["video_id"]
    (wd / "assets").mkdir(parents=True, exist_ok=True)
    describer, detector, session = make_describer(ctx.store), make_detector(), requests.Session()
    credits_path = wd / "image_credits.json"
    credits = json.loads(credits_path.read_text()) if credits_path.exists() else []
    report = []
    for i, s in enumerate(sb["scenes"], 1):
        v = s["visual"]
        if v["type"] != "image" or (v.get("asset") and (wd / "assets" / Path(v["asset"]).name).exists()):
            continue
        q = v.get("query") or s.get("on_screen_text", "")
        cands = []
        for search in (im.search_commons, im.search_openverse):
            try:
                cands += im.filter_candidates(search(q, session))
            except Exception as e:  # noqa: BLE001 - network/API errors are not fatal
                report.append({"scene": i, "warning": f"{search.__name__}: {str(e)[:120]}"})
            if len(cands) >= 4:
                break
        best, notes = im.choose(q, cands, wd, f"scene{i}", session, describer, detector=detector) if cands else (None, [])
        if best:
            v["asset"] = best.file
            v.setdefault("caption", "")
            if (best.vision or {}).get("focus"):
                v["focus"] = best.vision["focus"]      # renderer zooms toward this box
            credits.append({"type": "image", "file": best.file, "credit": best.credit,
                            "creator": best.creator, "license": best.license, "page_url": best.page_url,
                            "needs_attribution": best.needs_attribution or best.requires_permission,
                            "requires_permission": best.requires_permission,
                            "visual_unverified": best.visual_unverified,
                            "description": best.caption, "vision": best.vision})
            # Claude only reads this compact line (~30-40 tokens); details stay in image_credits.json
            report.append({"s": i, "f": best.file, "lic": best.license[:16],
                           "perm": int(best.requires_permission), **summarize(best.vision or {})})
        else:
            s["visual"] = {"type": "title", "headline": s.get("on_screen_text") or q[:40]}
            report.append({"s": i, "fallback": "title"})
    credits_path.write_text(json.dumps(credits, indent=1))
    Path(a.storyboard).write_text(json.dumps(sb, indent=1, ensure_ascii=False))
    out({"ok": True, "describer": describer.name, "scenes": report})
    return 0


def cmd_fetch_asset(ctx: Ctx, a) -> int:
    """Your own image from Drive 01_Inbox/References/Images (needs RIGHTS: line in description)."""
    from studio.video.source_video import rights_declaration
    meta = ctx.drive.metadata(a.file_id)
    if ctx.s.folders.get("references_images") not in meta.get("parents", []):
        out({"ok": False, "error": "file is not in References/Images"})
        return 2
    rights = rights_declaration(meta.get("description"))
    if not rights:
        out({"ok": False, "error": "add 'RIGHTS: OWNED' or 'RIGHTS: LICENSED <note>' to the Drive description"})
        return 2
    wd = WORK / a.video_id / "assets"
    name = f"{a.name}{Path(meta['name']).suffix.lower() or '.jpg'}"
    ctx.drive.download(a.file_id, wd / name)
    from PIL import Image
    with Image.open(wd / name) as img:
        img.verify()
    cp = WORK / a.video_id / "image_credits.json"
    credits = json.loads(cp.read_text()) if cp.exists() else []
    credits.append({"type": "image", "file": name, "credit": f"Own image ({rights})", "license": rights,
                    "needs_attribution": rights.startswith("LICENSED"), "visual_unverified": False})
    cp.write_text(json.dumps(credits, indent=1))
    out({"ok": True, "asset": name, "rights": rights})
    return 0


def cmd_frames(ctx: Ctx, a) -> int:
    """Director review: one image with 2 frames per scene of the rendered video."""
    from studio.video import reference as ref
    wd = WORK / a.video_id
    timings = json.loads((wd / "timings.json").read_text())["timings"]
    sheet = ref.scene_frames(wd / f"{a.video_id}.mp4", timings, wd / "review_sheet.png")
    rep = wd / "custom_scenes_report.json"
    out({"ok": True, "sheet": str(sheet), "scenes": len(timings),
         "custom_scenes": json.loads(rep.read_text()) if rep.exists() else []})
    return 0


def cmd_scene_frames(ctx: Ctx, a) -> int:
    """Per-scene review BEFORE the full render: build the animation from the cached voice and
    snapshot each scene (3 stills) with HyperFrames, one strip image per scene for the
    scene-reviewer. Costs no voice characters (cache) and no full render."""
    from studio.audio.tts import for_language, synthesize_all
    from studio.audio import mastering as audio
    from studio.video import brand as brandmod
    from studio.subtitles import captions
    from studio.video import hyperframes as hfr
    sb = sbmod.normalize(load_json(a.storyboard))
    rec = ctx.store.get(a.video_id)
    wd = WORK / rec.video_id
    long_, lang = sbmod.is_long(sb), sb.get("language", "en")
    full = sbmod.with_outro(sb, ctx.s.outro_for(long_, lang)) if ctx.s.outro_enabled else sb
    clips, _, _ = synthesize_all([x["narration"] for x in full["scenes"]], wd, for_language(ctx.s, lang), ctx.store,
                                 log=lambda m: print(m, file=sys.stderr))
    timings = audio.build_timeline(clips, wd / "narration_raw.wav")
    caps = captions.build(full["scenes"], timings)
    logo = f"assets/{brandmod.LOGO_NAME}" if brandmod.logo_path() else None
    if logo:
        (wd / "assets").mkdir(exist_ok=True)
        shutil.copy(brandmod.logo_path(), wd / "assets" / brandmod.LOGO_NAME)
    proj = hfr.build(full, timings, caps, wd, (sb.get("series_label") or "TECH").upper(), logo=logo)
    hf = str(hfr.RENDERER_DIR / "node_modules" / ".bin" / "hyperframes")
    want = [int(x) for x in str(a.scene).split(",")] if a.scene != "all" else range(1, len(full["scenes"]) + 1)
    times = {}
    for n in want:                                   # 3 stills per scene, ONE browser session for all
        tm = timings[n - 1]
        d = tm["end"] - tm["start"]
        times[n] = [round(tm["start"] + f * d, 2) for f in (0.2, 0.55, 0.9)]
    snap = wd / "scene_frames" / "stills"
    if snap.exists():
        shutil.rmtree(snap)
    allt = ",".join(f"{t:.2f}" for ts in times.values() for t in ts)
    subprocess.run([hf, "snapshot", str(proj), "--at", allt, "--no-end", "--keep", "--output", str(snap),
                    "--timeout", "170000"], capture_output=True, text=True, timeout=3600)
    def still(t):
        return snap / f"at-{(f'{t:.2f}').rstrip('0').rstrip('.')}s.png"
    sheets = []
    for n, ts in times.items():
        pngs = [still(t) for t in ts if still(t).exists()]
        if not pngs:
            sheets.append({"scene": n, "error": "snapshot failed"})
            continue
        strip = wd / "scene_frames" / f"scene{n}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", *sum([["-i", str(p)] for p in pngs], []), "-filter_complex",
                        "".join(f"[{k}:v]scale=-2:{360 if long_ else 640}[v{k}];" for k in range(len(pngs)))
                        + "".join(f"[v{k}]" for k in range(len(pngs))) + f"hstack=inputs={len(pngs)}",
                        str(strip)], check=False)
        sheets.append({"scene": n, "strip": str(strip), "narration": full["scenes"][n - 1]["narration"][:160]})
    out({"ok": True, "scenes": sheets, "next": "scene-reviewer per strip → fix storyboard/scene code → re-run for that scene"})
    return 0


def register(sub) -> None:
    p = sub.add_parser("validate-storyboard"); p.add_argument("--file", required=True); p.add_argument("--document-based", action="store_true")
    p = sub.add_parser("render"); p.add_argument("--video-id", required=True); p.add_argument("--storyboard", required=True)
    p.add_argument("--document-based", action="store_true")
    p = sub.add_parser("texts"); p.add_argument("--storyboard", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("voice"); p.add_argument("--video-id", required=True); p.add_argument("--storyboard", required=True)
    p = sub.add_parser("localize"); p.add_argument("--video-id", required=True); p.add_argument("--storyboard")
    p.add_argument("--texts", help="translated text list (from `texts`); visuals copied from the English storyboard")
    p.add_argument("--lang", default="ta", choices=["ta"]); p.add_argument("--metadata")
    p = sub.add_parser("resolve-images"); p.add_argument("--storyboard", required=True)
    p = sub.add_parser("fetch-asset"); p.add_argument("--video-id", required=True); p.add_argument("--file-id", required=True)
    p.add_argument("--name", required=True)
    p = sub.add_parser("frames"); p.add_argument("--video-id", required=True)
    p = sub.add_parser("scene-frames"); p.add_argument("--video-id", required=True); p.add_argument("--storyboard", required=True)
    p.add_argument("--scene", default="all", help="1-based scene numbers, comma separated, or all")

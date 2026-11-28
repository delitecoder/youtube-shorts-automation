"""`studio` commands: plan (experiment + style + novelty), novelty, experiment, facts."""
from __future__ import annotations

import json
from pathlib import Path

from studio.analytics import facts as factmod
from studio.animation import styles
from studio.cli.common import WORK, load_json, out
from studio.experimentation import engine
from studio.memory import content as memory
from studio.storage.store import is_long


def cmd_plan(ctx, a) -> int:
    """Before scripting: experiment assignment (70/20/10 policy) + the style for this video, and (with
    --storyboard) write the explicit style into the storyboard and run the originality check."""
    policy = engine.plan(ctx.store, a.video_id)
    fmt = "long" if is_long(a.video_id) else "short"
    history = ctx.store.rows("ProductionManifest")
    assigned = policy["choices"].get("style_id") if policy.get("mode") != "proven" else None
    style, why = styles.choose(a.category or "", history, fmt, assigned=assigned)
    res = {"ok": True, "video_id": a.video_id, "policy": policy, "style": style["id"], "style_reason": why,
           "brief": {k: style.get(k) for k in ("camera_language", "motion_principles", "typography", "techniques",
                                               "audio_traits")}}
    if a.storyboard:
        sbp = Path(a.storyboard)
        sb = styles.apply(load_json(a.storyboard), style)
        ch = policy["choices"]
        sb.setdefault("story_structure", ch.get("story_structure"))
        sb.setdefault("hook_type", ch.get("hook_type"))
        sb["experiment"] = {"exp_id": policy.get("exp_id"), "arm": policy.get("arm"), "mode": policy.get("mode")}
        sbp.write_text(json.dumps(sb, ensure_ascii=False, indent=1))
        res["novelty"] = memory.check(ctx.store, sb, WORK / a.video_id / "scenes", exclude_video=a.video_id)
        res["ok"] = not res["novelty"]["block"]
    out(res)
    return 0 if res["ok"] else 2


def cmd_novelty(ctx, a) -> int:
    sb = load_json(a.storyboard)
    vid = a.video_id or sb.get("video_id", "")
    r = memory.check(ctx.store, sb, WORK / vid / "scenes" if vid else None, exclude_video=vid)
    out({"ok": not r["block"], **r})
    return 0 if not r["block"] else 2


def cmd_experiment(ctx, a) -> int:
    if a.action == "evaluate":
        res = engine.evaluate(ctx.store)
        from studio.notifications.templates import notify
        for r in res:
            if r.get("status") == "concluded":
                notify(ctx.store, "experiment_results", exp_id=r["exp_id"], text=r["conclusion"],
                       items={"variable": r["dimension"], "control": r["control"], "treatment": r["treatment"],
                              "n": f"{r['n_control']}/{r['n_treatment']}", "CI": f"{r['ci_low']}..{r['ci_high']}"})
        out({"ok": True, "evaluated": [{k: r.get(k) for k in ("exp_id", "status", "n_control", "n_treatment",
                                                              "effect", "ci_low", "ci_high", "conclusion")} for r in res]})
        return 0
    exps = engine.latest_experiments(ctx.store)
    recs = engine.recommendations(ctx.store)
    out({"ok": True, "experiments": [{k: e.get(k) for k in ("exp_id", "status", "scope", "dimension", "control",
                                                           "treatment", "conclusion")} for e in exps.values()],
         "recommendations": [{k: r.get(k) for k in ("scope", "dimension", "choice", "evidence", "explanation")}
                             for r in recs.values()]})
    return 0


def cmd_facts(ctx, a) -> int:
    n = factmod.refresh(ctx.store)
    latest = factmod.latest(ctx.store)
    unknown = sorted({m for f in latest.values() for m in (f.get("unknown_metrics") or "").split(",") if m})
    out({"ok": True, "videos": n, "unknown_metrics": unknown,
         "note": "unknown metrics are not zero; they are excluded from experiments"})
    return 0


def register(sub) -> None:
    p = sub.add_parser("plan", help="experiment assignment + style + originality check for a video")
    p.add_argument("--video-id", required=True); p.add_argument("--category"); p.add_argument("--storyboard")
    p = sub.add_parser("novelty", help="originality check of a storyboard against content memory")
    p.add_argument("--storyboard", required=True); p.add_argument("--video-id")
    p = sub.add_parser("experiment", help="list | evaluate controlled experiments")
    p.add_argument("action", choices=["list", "evaluate"])
    sub.add_parser("facts", help="refresh VideoFacts from analytics snapshots (unknown != 0)")

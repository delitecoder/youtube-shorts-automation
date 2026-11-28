"""Controlled creative experiments: plan, assign, evaluate, recommend (config/experiments.yaml).

- One primary variable per experiment (a dimension such as hook_type), control vs treatment; every other
  choice follows the current recommendations (or production defaults).
- Allocation per video, deterministic from the video id: proven (use recommendations), variation (feed a
  running controlled experiment, balancing arms), exploration (an untested option, labelled, not evidence).
- Evaluation compares arm RESIDUALS against the same format+language baseline (median of other videos),
  with a bootstrap CI on the difference. A winner needs >= min_n per arm and a CI excluding 0; otherwise the
  result is inconclusive. Unknown metrics are skipped, never treated as 0. One video never decides anything.
"""
from __future__ import annotations

import hashlib
import json
import random
import statistics

from studio.analytics import facts as factmod
from studio.core import conf
from studio.core.state import iso, utcnow
from studio.storage.store import is_long, language_of


def _u(video_id: str, salt: str) -> float:
    return int(hashlib.sha256(f"{salt}:{video_id}".encode()).hexdigest()[:12], 16) / float(1 << 48)


def latest_experiments(store) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for r in store.rows("Experiments"):
        if r.get("exp_id") and r.get("updated_at", "") >= best.get(r["exp_id"], {}).get("updated_at", ""):
            best[r["exp_id"]] = r
    return best


def recommendations(store) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for r in store.rows("Recommendations"):
        k = f"{r.get('scope')}|{r.get('dimension')}"
        if r.get("updated_at", "") >= best.get(k, {}).get("updated_at", ""):
            best[k] = r
    return best


def _scope(video_id: str) -> str:
    return f"{'long' if is_long(video_id) else 'short'}:{language_of(video_id)}"


def _defaults(cfg: dict) -> dict:
    return {d: opts[0] for d, opts in cfg["dimensions"].items()}


def plan(store, video_id: str, cfg: dict | None = None) -> dict:
    """Production choices for this video + experiment assignment. Idempotent per video id."""
    c = cfg or conf.load("experiments")
    for a in store.rows("Assignments"):
        if a.get("video_id") == video_id:
            return json.loads(a.get("policy") or "{}") | {"exp_id": a.get("exp_id"), "arm": a.get("arm"), "reused": True}
    scope = _scope(video_id)
    recs = {k.split("|", 1)[1]: v["choice"] for k, v in recommendations(store).items() if k.startswith(scope + "|")}
    choices = _defaults(c) | recs
    u = _u(video_id, "alloc")
    al = c["allocation"]
    exps = latest_experiments(store)
    concluded_or_running = {e["dimension"] for e in exps.values() if e.get("scope") == scope}
    if u < al["proven"]:
        mode, exp_id, arm = "proven", "", ""
    elif u < al["proven"] + al["variation"]:
        mode = "variation"
        running = [e for e in exps.values() if e.get("status") == "running" and e.get("scope") == scope]
        if running:
            e = sorted(running, key=lambda x: x["exp_id"])[0]
        else:
            free = [d for d in c["dimensions"] if d not in concluded_or_running] or list(c["dimensions"])
            dim = free[int(_u(video_id, "dim") * len(free)) % len(free)]
            control = choices[dim]
            alts = [o for o in c["dimensions"][dim] if o != control]
            treatment = alts[int(_u(video_id, "alt") * len(alts)) % len(alts)]
            e = {"exp_id": f"EXP-{scope.replace(':', '-')}-{dim}-{len(exps) + 1}", "status": "running",
                 "dimension": dim, "hypothesis": f"{dim}={treatment} improves {c['primary_metric']} vs {control}",
                 "variable": dim, "control": control, "treatment": treatment, "scope": scope,
                 "metric": c["primary_metric"], "min_n": str(c["min_n_per_arm"]), "n_control": "0", "n_treatment": "0",
                 "effect": "", "ci_low": "", "ci_high": "", "conclusion": "running", "evidence": ""}
            store.append("Experiments", e | {"updated_at": iso(utcnow())})
        counts = {"control": 0, "treatment": 0}
        for a in store.rows("Assignments"):
            if a.get("exp_id") == e["exp_id"] and a.get("arm") in counts:
                counts[a["arm"]] += 1
        arm = "control" if counts["control"] < counts["treatment"] else (
            "treatment" if counts["treatment"] < counts["control"] else ("treatment" if _u(video_id, "arm") < 0.5 else "control"))
        choices[e["dimension"]] = e[arm]
        exp_id = e["exp_id"]
    else:
        mode = "exploration"
        dim = list(c["dimensions"])[int(_u(video_id, "xdim") * len(c["dimensions"])) % len(c["dimensions"])]
        opts = [o for o in c["dimensions"][dim] if o != choices[dim]]
        choices[dim] = opts[int(_u(video_id, "xopt") * len(opts)) % len(opts)]
        exp_id, arm = "", f"explore:{dim}"
    policy = {"mode": mode, "choices": choices, "scope": scope}
    store.append("Assignments", {"assigned_at": iso(utcnow()), "video_id": video_id, "exp_id": exp_id, "arm": arm,
                                 "policy": json.dumps(policy)})
    return policy | {"exp_id": exp_id, "arm": arm, "reused": False}


def _bootstrap_ci(a: list[float], b: list[float], conf_level: float, n: int = 2000, seed: int = 7) -> tuple[float, float, float]:
    rnd = random.Random(seed)
    diffs = sorted(statistics.fmean(rnd.choices(b, k=len(b))) - statistics.fmean(rnd.choices(a, k=len(a)))
                   for _ in range(n))
    lo = diffs[int((1 - conf_level) / 2 * n)]
    hi = diffs[int((1 + conf_level) / 2 * n) - 1]
    return statistics.fmean(b) - statistics.fmean(a), lo, hi


def evaluate(store, cfg: dict | None = None, min_days_live: int = 7) -> list[dict]:
    c = cfg or conf.load("experiments")
    fx = factmod.latest(store)
    by_scope: dict[str, list[float]] = {}
    for vid, f in fx.items():
        v = factmod.metric(f, c["primary_metric"])
        if v is not None:
            by_scope.setdefault(_scope(vid), []).append(v)
    results = []
    for e in latest_experiments(store).values():
        if e.get("status") != "running":
            continue
        base = statistics.median(by_scope.get(e["scope"], [0.0]) or [0.0])
        arms: dict[str, list[float]] = {"control": [], "treatment": []}
        used, unknown = [], []
        for a in store.rows("Assignments"):
            if a.get("exp_id") != e["exp_id"] or a.get("arm") not in arms:
                continue
            f = fx.get(a["video_id"])
            v = factmod.metric(f, c["primary_metric"]) if f else None
            if f is None or v is None or int(f.get("days_live") or 0) < min_days_live:
                unknown.append(a["video_id"])
                continue
            arms[a["arm"]].append(v - base)
            used.append(a["video_id"])
        n_c, n_t, min_n = len(arms["control"]), len(arms["treatment"]), int(e.get("min_n") or c["min_n_per_arm"])
        row = dict(e) | {"n_control": str(n_c), "n_treatment": str(n_t),
                         "evidence": json.dumps({"videos": used, "unknown_or_too_young": unknown, "baseline": base})}
        if n_c >= min_n and n_t >= min_n:
            eff, lo, hi = _bootstrap_ci(arms["control"], arms["treatment"], float(c["confidence"]))
            row |= {"effect": f"{eff:.3f}", "ci_low": f"{lo:.3f}", "ci_high": f"{hi:.3f}"}
            if lo > 0 or hi < 0:
                win = e["treatment"] if lo > 0 else e["control"]
                row |= {"status": "concluded", "conclusion": f"{win} better on {e['metric']} ({eff:+.2f}, CI {lo:.2f}..{hi:.2f})"}
                store.append("Recommendations", {"updated_at": iso(utcnow()), "scope": e["scope"],
                                                 "dimension": e["dimension"], "choice": win, "confidence": c["confidence"],
                                                 "evidence": e["exp_id"], "explanation": row["conclusion"]})
            elif n_c >= 3 * min_n and n_t >= 3 * min_n:
                row |= {"status": "concluded", "conclusion": f"no meaningful difference (CI {lo:.2f}..{hi:.2f}); keep control"}
            else:
                row |= {"conclusion": f"inconclusive so far (CI {lo:.2f}..{hi:.2f} includes 0); continue"}
        else:
            row |= {"conclusion": f"inconclusive: n={n_c}/{n_t}, need {min_n} per arm"}
        store.append("Experiments", row | {"updated_at": iso(utcnow())})
        results.append(row)
    return results

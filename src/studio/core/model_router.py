"""Model routing (config/models.yaml): which model handles a task, with bounded escalation and fallbacks.

Deterministic work never gets a model. Escalation goes one tier up per hop, at most `max_attempts` hops,
and only for listed reasons. An unavailable model falls back to its configured fallback (never upward to
a more expensive tier unless no cheaper option exists), and every decision carries a human-readable reason.
"""
from __future__ import annotations

from dataclasses import dataclass

from studio.core import conf


class RoutingError(ValueError):
    pass


@dataclass(frozen=True)
class Route:
    task: str
    model: str | None          # None = deterministic Python, no model
    max_output_tokens: int
    retries: int
    reason: str


def _cfg(cfg: dict | None) -> dict:
    return cfg if cfg is not None else conf.load("models")


def route(task: str, *, available: set[str] | None = None, attempt: int = 0, why: str = "",
          cfg: dict | None = None) -> Route:
    c = _cfg(cfg)
    if task in c.get("deterministic", []):
        return Route(task, None, 0, 0, "deterministic task: Python, no model")
    spec = c["tasks"].get(task)
    if spec is None:
        raise RoutingError(f"unknown task {task!r}; add it to config/models.yaml")
    model, reason = spec["model"], f"default for {task}"
    max_hops = c.get("escalation", {}).get("max_attempts", 2)
    if attempt:
        if attempt > max_hops:
            raise RoutingError(f"{task}: escalation limit ({max_hops}) reached")
        if why not in c.get("escalation", {}).get("reasons", []):
            raise RoutingError(f"{task}: escalation reason {why!r} not allowed")
        for _ in range(attempt):
            nxt = c["tasks"][task]["escalate_to"] if model == spec["model"] else _next_tier(c, model)
            if not nxt:
                raise RoutingError(f"{task}: no higher tier than {model}")
            model = nxt
        reason = f"escalated x{attempt} ({why})"
    if available is not None and model not in available:
        seen = {model}
        while model not in available:
            fb = c["models"][model].get("fallback")
            if not fb or fb in seen:
                raise RoutingError(f"{task}: no available model (tried {sorted(seen)})")
            seen.add(fb)
            model = fb
        reason += f"; fallback to {model} (unavailable: {sorted(seen - {model})})"
    return Route(task, c["models"][model]["alias"], spec["max_output_tokens"], spec["retries"], reason)


def _next_tier(c: dict, model: str) -> str | None:
    tier = c["models"][model]["tier"]
    ups = sorted((m["tier"], k) for k, m in c["models"].items() if m["tier"] > tier and not m.get("requires_credits"))
    return ups[0][1] if ups else None


def table(cfg: dict | None = None) -> list[dict]:
    """Routing table for docs/MODEL_ROUTING.md and `studio route`."""
    c = _cfg(cfg)
    rows = [{"task": t, "model": s["model"], "max_output_tokens": s["max_output_tokens"],
             "retries": s["retries"], "escalate_to": s["escalate_to"]} for t, s in c["tasks"].items()]
    rows += [{"task": t, "model": "python", "max_output_tokens": 0, "retries": 0, "escalate_to": None}
             for t in c.get("deterministic", [])]
    return rows

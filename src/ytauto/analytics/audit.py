"""Daily views audit: what is working, what is not, and concrete changes for the next video.

Runs every day before production (pure Python + YouTube Analytics API, no Claude tokens):
  * channel trend: last 7 / 28 days vs the previous period (views, watch time, subscribers)
  * every video from the last 90 days: views, average % viewed, avg duration, likes, comments,
    shares, subscribers gained -> top and bottom performers
  * traffic sources (Shorts feed, search, browse, external …) for the last 28 days
  * our pipeline's own videos compared with the channel median
Rules turn the numbers into short, specific suggestions. The compact `guidance` block is passed
to the storywriter so each new video applies them; the owner gets a daily audit email.
Only metrics returned by the API are used; nothing is estimated or invented.
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import median

VIDEO_METRICS = "views,averageViewPercentage,averageViewDuration,likes,comments,shares,subscribersGained"


def _q(yta, **kw):
    try:
        return yta.reports().query(ids="channel==MINE", **kw).execute()
    except Exception as e:  # noqa: BLE001 - one failing query must not kill the audit
        return {"error": str(e)[:200]}


def _rows(res) -> list[dict]:
    if not res.get("rows"):
        return []
    hdr = [h["name"] for h in res["columnHeaders"]]
    return [dict(zip(hdr, r)) for r in res["rows"]]


def period(yta, start: date, end: date) -> dict:
    r = _rows(_q(yta, startDate=str(start), endDate=str(end),
                 metrics="views,estimatedMinutesWatched,subscribersGained,likes,shares"))
    return r[0] if r else {}


def collect(yta, yt, today: date, pipeline_ids: set[str]) -> dict:
    d = today - timedelta(days=1)                       # analytics lag: use complete days
    rep = {"date": str(today)}
    rep["last7"] = period(yta, d - timedelta(days=6), d)
    rep["prev7"] = period(yta, d - timedelta(days=13), d - timedelta(days=7))
    rep["last28"] = period(yta, d - timedelta(days=27), d)
    rep["prev28"] = period(yta, d - timedelta(days=55), d - timedelta(days=28))
    vids = _rows(_q(yta, startDate=str(d - timedelta(days=89)), endDate=str(d), metrics=VIDEO_METRICS,
                    dimensions="video", sort="-views", maxResults=50))
    titles = {}
    ids = [v["video"] for v in vids]
    for i in range(0, len(ids), 50):
        for it in yt.svc.videos().list(part="snippet,contentDetails", id=",".join(ids[i:i + 50])).execute().get("items", []):
            titles[it["id"]] = {"title": it["snippet"]["title"], "published": it["snippet"]["publishedAt"][:10]}
    for v in vids:
        v.update(titles.get(v["video"], {}))
        v["ours"] = v["video"] in pipeline_ids
    rep["videos"] = vids
    rep["traffic"] = {r["insightTrafficSourceType"]: r["views"] for r in _rows(_q(
        yta, startDate=str(d - timedelta(days=27)), endDate=str(d), metrics="views",
        dimensions="insightTrafficSourceType", sort="-views"))}
    return rep


def _pct(a, b):
    return None if not b else round(100 * (a - b) / b, 1)


def analyse(rep: dict) -> dict:
    vids = rep.get("videos", [])
    out = {"date": rep["date"], "trend": {}, "top": [], "bottom": [], "suggestions": [], "guidance": {}}
    for k in ("views", "estimatedMinutesWatched", "subscribersGained"):
        out["trend"][f"{k}_7d"] = rep.get("last7", {}).get(k)
        out["trend"][f"{k}_7d_change_pct"] = _pct(rep.get("last7", {}).get(k, 0), rep.get("prev7", {}).get(k, 0))
        out["trend"][f"{k}_28d_change_pct"] = _pct(rep.get("last28", {}).get(k, 0), rep.get("prev28", {}).get(k, 0))
    if not vids:
        out["suggestions"].append("No video data in the last 90 days yet; keep publishing daily at the peak slot.")
        return out
    med = {k: median(float(v.get(k) or 0) for v in vids) for k in
           ("views", "averageViewPercentage", "averageViewDuration", "likes", "shares", "subscribersGained")}
    out["channel_median"] = {k: round(v, 1) for k, v in med.items()}

    def brief(v):
        return {"title": v.get("title", v["video"])[:70], "views": v["views"], "avg_view_pct": round(v["averageViewPercentage"], 1),
                "avg_sec": v["averageViewDuration"], "likes": v["likes"], "subs": v["subscribersGained"], "ours": v["ours"]}
    ranked = sorted(vids, key=lambda v: (v["views"], v["averageViewPercentage"]), reverse=True)
    out["top"] = [brief(v) for v in ranked[:3]]
    out["bottom"] = [brief(v) for v in ranked[-3:]] if len(ranked) > 3 else []
    ours = [v for v in vids if v["ours"]]
    if ours:
        out["ours_vs_channel"] = {"videos": len(ours), "median_views": median(v["views"] for v in ours),
                                  "median_avg_view_pct": round(median(v["averageViewPercentage"] for v in ours), 1)}

    s, g = out["suggestions"], out["guidance"]
    avp = med["averageViewPercentage"]
    if avp < 60:
        s.append(f"Viewers watch only {avp:.0f}% on average: open with the payoff in the first 2 seconds, "
                 "cut the intro, keep it 40–50 s.")
        g["target_seconds"] = 45
        g["hook"] = "state the surprising result in the first sentence; no setup"
    elif avp < 80:
        s.append(f"Average {avp:.0f}% viewed: tighten the middle (one idea per scene) and change the visual every 5–7 s.")
        g["target_seconds"] = 50
    else:
        s.append(f"Strong retention ({avp:.0f}% viewed): keep the current pacing; test 55–60 s stories.")
        g["target_seconds"] = 55
    total = sum(rep.get("traffic", {}).values()) or 0
    if total:
        feed = rep["traffic"].get("SHORTS", 0) / total
        search = rep["traffic"].get("YT_SEARCH", 0) / total
        out["traffic_share"] = {k: round(v / total, 2) for k, v in rep["traffic"].items()}
        if feed < 0.5:
            s.append(f"Only {feed:.0%} of views come from the Shorts feed: the first frame and first line must stop "
                     "the scroll (big bold claim on screen at 0 s, motion immediately).")
            g["first_frame"] = "bold 3–5 word claim on screen at 0 s"
        if search > 0.15:
            s.append(f"{search:.0%} of views come from search: put the exact search phrase first in the title and first line.")
            g["seo"] = "exact search phrase first in title and description line 1"
    likes_rate = med["likes"] / med["views"] if med["views"] else 0
    if likes_rate < 0.03:
        s.append("Few likes per view: end with a specific, easy-to-answer question and pin it as a comment.")
        g["cta"] = "specific one-word-answer question at the end"
    if med["subscribersGained"] < 1:
        s.append("Few new subscribers: make each video part of a recognisable daily series (same title prefix/visual style).")
        g["series"] = "consistent series label and opening style"
    if out["top"]:
        g["learn_from_top_titles"] = [t["title"] for t in out["top"]]
    wk = out["trend"].get("views_7d_change_pct")
    if wk is not None and wk < -20:
        s.append(f"Views dropped {abs(wk):.0f}% week over week: try a more timely topic (news of the last 7 days) next.")
        g["topic"] = "prefer a timely topic from the last 7 days"
    return out


def email_text(a: dict) -> str:
    t = a["trend"]
    lines = [f"Daily views audit — {a['date']}",
             f"Views last 7 days: {t.get('views_7d')} ({_fmt(t.get('views_7d_change_pct'))} vs previous week, "
             f"{_fmt(t.get('views_28d_change_pct'))} over 28 days)",
             f"New subscribers last 7 days: {t.get('subscribersGained_7d')}"]
    if a.get("top"):
        lines.append("Top videos (90 days): " + "; ".join(f"{v['title']} — {v['views']} views, {v['avg_view_pct']}% watched"
                                                         for v in a["top"]))
    if a.get("traffic_share"):
        lines.append("Where views come from: " + ", ".join(f"{k} {v:.0%}" for k, v in
                                                          sorted(a["traffic_share"].items(), key=lambda x: -x[1])[:4]))
    lines.append("What the next videos will change:")
    lines += [f"  • {x}" for x in a["suggestions"]]
    return "\n".join(lines)


def _fmt(p):
    return "n/a" if p is None else f"{p:+.0f}%"

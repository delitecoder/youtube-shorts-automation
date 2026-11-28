"""Reddit promotion DRAFTS for manual review. Nothing is ever posted to Reddit automatically.

The storywriter agent supplies community-specific drafts as JSON:
  [{"subreddit": "learnprogramming", "rules_checked": ["no self-promo on weekdays", ...],
    "self_promo_allowed": true, "title": "...", "body": "..."}]
This module validates them and renders one Markdown file that is stored in Drive.
"""
from __future__ import annotations

import json


def render(video_id: str, youtube_url: str, drafts: list[dict]) -> str:
    out = [f"# Reddit drafts for {video_id}", "",
           f"Video: {youtube_url}", "",
           "**Manual review only.** Read each community's current rules before posting. "
           "Do not post the same text to several subreddits.", ""]
    for d in drafts:
        for k in ("subreddit", "title", "body", "rules_checked", "self_promo_allowed"):
            if k not in d:
                raise ValueError(f"draft missing '{k}'")
        if not d["self_promo_allowed"]:
            continue
        out += [f"## r/{d['subreddit']}", "", "Rules checked: " + "; ".join(d["rules_checked"]), "",
                f"**Title:** {d['title']}", "", d["body"], "", "---", ""]
    return "\n".join(out)


def load(path) -> list[dict]:
    data = json.loads(open(path).read())
    if not isinstance(data, list):
        raise ValueError("drafts file must be a JSON list")
    return data

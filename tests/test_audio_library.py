"""Licensed audio selection + story sound cues."""
from studio.audio import library as al
from studio.audio import mix as sound


def _a(i, cat, kind="sfx", lic="CC0-1.0", tags=()):
    return {"id": i, "title": i, "category": cat, "kind": kind, "tags": list(tags), "duration": 1.0, "path": f"audio/{i}.ogg",
            "license": {"id": lic, "commercial_use": True, "modifications_allowed": True, "attribution": f"{i} by X",
                        "attribution_required": lic != "CC0-1.0"}}


def test_select_maps_categories_and_skips_bad_licenses():
    cat = [_a("swoosh-1", "whoosh"), _a("kenney-digital-high-up", "digital-game"), _a("kenney-digital-high-down", "digital-game"),
           _a("step-1", "footstep"), _a("bgm-investigations", "comedy", "music", "CC-BY-4.0"),
           dict(_a("nc-swoosh", "whoosh"), license={"id": "CC-BY-NC-4.0", "commercial_use": False})]
    got = {(a["id"], k) for a, k, _ in al.select(cat)}
    assert ("swoosh-1", "whoosh") in got and ("kenney-digital-high-up", "success") in got
    assert ("kenney-digital-high-down", "fail") in got and ("step-1", "footstep") in got
    assert ("bgm-investigations", "music") in got and not any(i == "nc-swoosh" for i, _ in got)


def test_story_cues_follow_visible_actions():
    v = {"type": "story", "location": "server_room", "cast": [
        {"who": "arjun", "walk_from": "left", "say": "Hi", "say_at": 0.5,
         "actions": [{"at": 0, "pose": "stand"}, {"at": 0.5, "pose": "facepalm"}]},
        {"who": "byte", "mood": [{"at": 0.3, "face": "overloaded"}]}]}
    cues = sound.plan_cues([{"visual": v}], [{"start": 10.0, "end": 14.0}])
    kinds = [k for _, k in cues]
    assert "amb_machine" in kinds and kinds.count("footstep") == 4 and "pop" in kinds and kinds.count("fail") == 2

"""Stickman world: every location builds, the cast keeps its look, speech and camera are animated."""
from ytauto.production import world


def test_every_location_and_cast_member_builds():
    for loc in world.LOCATIONS:
        for long_ in (False, True):
            v = {"type": "story", "location": loc, "sign": "Hello", "camera": "push",
                 "cast": [{"who": "arjun", "x": 0.3, "say": "Why?", "walk_from": "left",
                           "actions": [{"at": 0, "pose": "stand", "face": "worried"}, {"at": 0.5, "pose": "point", "face": "happy"}]},
                          {"who": "priya", "x": 0.6, "face": "left"}, {"who": "byte", "x": 0.85, "mood": [{"at": 0.2, "face": "overloaded"}]}]}
            html, js = world.visual(2, v, 10.0, 5.0, long_, 7)
            assert 's2c0-torso' in html and "#2f6fde" in html            # Arjun's hoodie colour
            assert "s2c1" in html and "#f5a524" in html                  # Priya's scarf
            assert 's2c2-body' in html and "Why?" in html and "Hello" in html
            assert any("#s2w-cam" in x for x in js) and any("s2c0-say" in x for x in js)


def test_raju_and_unknown_location_fall_back():
    html, _ = world.visual(0, {"type": "story", "location": "moon", "cast": [{"who": "raju"}]}, 0, 3, False, 1)
    assert "#3bb273" in html and "s0w-sec" in html                       # office clock, Raju's cap colour


def test_engine_guarantees_from_scene_review():
    # dark places: chalk ink so the cast stays visible
    html, _ = world.visual(0, {"type": "story", "location": "server_room", "cast": [{"who": "arjun"}]}, 0, 3, True, 1)
    assert world.CHALK in html and "s0w-spot" in html
    # never standing inside the office desk; characters kept apart
    xs = world.place_cast([0.7, 0.72, 0.3], world.NO_STAND["office"])
    assert all(not (0.5 < x < 0.95) for x in xs) and min(abs(a - b) for a in xs for b in xs if a != b) >= 0.15
    # a bubble that would cover the sign goes beside the head (top-aligned wrapper)
    html, _ = world.visual(0, {"type": "story", "location": "office", "sign": "Loading", "cast": [
        {"who": "arjun", "x": 0.5, "say": "Still loading"}]}, 0, 3, True, 1)
    assert "w-top" in html
    # camera zoom stays gentle
    assert max(v[1] for v in world.CAMERA.values()) <= 1.1

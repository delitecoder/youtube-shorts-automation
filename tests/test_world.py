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

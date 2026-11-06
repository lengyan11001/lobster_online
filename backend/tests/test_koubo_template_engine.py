from __future__ import annotations

from backend.app.services import koubo_template_engine as e


def test_public_templates_puts_default_first():
    templates = e.public_templates()
    assert len(templates) >= 3
    assert templates[0]["id"] == "premium_kinetic_v1"
    assert templates[0]["default"] is True
    assert all(t["id"] and t["name"] and t["description"] and t.get("cover") for t in templates)


def test_ensure_keywords_has_positive_duration():
    timeline = {
        "segs": [
            {"start": 0.0, "end": 8.0, "text": "全身边走边介绍apec"},
        ],
        "keywords": [],
    }
    e._ensure_keywords(timeline)
    assert timeline["keywords"]
    kw = timeline["keywords"][0]
    assert kw["start"] == 2.2
    assert kw["end"] > kw["start"]
    assert kw["end"] <= 8.0


def test_title_is_short_and_clean():
    spec = e.get_template("premium_kinetic_v1")
    assert spec
    project = {"segments": [{"text": "全身边走边介绍 APEC！"}], "name": "x"}
    title = e._title_for(project, spec, "我的一段标题")
    assert title == "我的一段标题"
    title = e._title_for(project, spec)
    assert len(title) <= int(spec["rules"]["titleChars"])
    assert " " not in title and "！" not in title


def test_canvas_keeps_source_even_and_supports_ratios():
    assert e._canvas({"ratio": "9:16"}, {"w": 400, "h": 736}) == (720, 1280)
    assert e._canvas({"ratio": "16:9"}, {"w": 400, "h": 736}) == (1280, 720)
    assert e._canvas({"ratio": "source"}, {"w": 401, "h": 737}) == (400, 736)

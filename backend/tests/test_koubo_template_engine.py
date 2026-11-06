from __future__ import annotations

from backend.app.services import koubo_template_engine as e


def test_public_templates_puts_default_first():
    templates = e.public_templates()
    assert len(templates) >= 30
    assert templates[0]["id"] == "premium_kinetic_v1"
    assert templates[0]["default"] is True
    assert len([t for t in templates if t.get("featured")]) == 6
    assert len({t["category"] for t in templates}) >= 6
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


def test_recommend_templates_matches_script_keywords():
    from backend.app.api.skill_deps import RecommendBody, koubo_recommend_templates

    commerce = koubo_recommend_templates(RecommendBody(text="秒杀价 99 元 限时优惠 下单", limit=3))
    assert commerce["matched_categories"][:1] == ["带货转化"]
    assert len(commerce["items"]) == 3
    assert commerce["items"][0]["category"] == "带货转化"
    assert len({x["template_id"] for x in commerce["items"]}) == 3

    howto = koubo_recommend_templates(RecommendBody(text="一分钟学会操作步骤", limit=3))
    assert howto["matched_categories"][:1] == ["知识教程"]
    assert howto["items"][0]["category"] == "知识教程"

    generic = koubo_recommend_templates(RecommendBody(text="今天天气不错", limit=3))
    assert generic["matched_categories"] == []
    assert len(generic["items"]) == 3
    assert generic["items"][0]["reason"] == "通用口播模板"
    assert koubo_recommend_templates(RecommendBody(text="", limit=99))["items"]


def test_template_detail_exposes_scenes_and_motion():
    from backend.app.services import koubo_template_engine as e

    d = e.template_detail("premium_kinetic_v1")
    assert d and d["id"] == "premium_kinetic_v1"
    assert d["layout"] == "kinetic"
    assert d["scenes"] and d["motion_label"] == "镜头缓推"
    assert d["transition_label"] == "闪白转场"
    assert "字幕安全区" in d["features"]
    assert e.template_detail("does_not_exist") is None


def test_batch_render_runs_selected_projects(tmp_path, monkeypatch):
    import time

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import skill_deps as s

    monkeypatch.setattr(s, "OUTPUT_DIR", tmp_path)
    (tmp_path / "p1").mkdir()
    (tmp_path / "p1" / "final.mp4").write_bytes(b"x")
    monkeypatch.setattr(s, "_load_projects", lambda: [{"id": "p1", "name": "第一条"}])

    def fake_render(job_id, *args, **kwargs):
        job = s._RENDER_JOBS[job_id]
        job["status"] = "done"
        job["progress"] = 100
        job["message"] = "预览完成"
        job["qa"] = {"ok": True, "score": 96}
        job["finished_at"] = time.time()

    monkeypatch.setattr(s, "_render_job", fake_render)
    app = FastAPI()
    app.include_router(s.router)
    client = TestClient(app)
    r = client.post("/api/skills/digital-human-koubo/batch-render",
                    json={"project_ids": ["p1", "p1"], "template_id": "premium_kinetic_v1", "quality": "preview"})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    job_id = r.json()["job_id"]
    data = {}
    for _ in range(60):
        data = client.get("/api/skills/digital-human-koubo/batch-render/jobs/%s" % job_id).json()
        if data["status"] != "running":
            break
        time.sleep(0.1)
    assert data["status"] == "done"
    assert data["items"][0]["status"] == "done"
    assert data["items"][0]["score"] == 96
    assert data["items"][0]["output"].endswith("/preview")


def test_batch_render_rejects_empty_or_unknown_projects():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import skill_deps as s

    app = FastAPI()
    app.include_router(s.router)
    client = TestClient(app)
    empty = client.post("/api/skills/digital-human-koubo/batch-render", json={"project_ids": []})
    assert empty.status_code == 400
    unknown = client.post("/api/skills/digital-human-koubo/batch-render", json={"project_ids": ["nope"]})
    assert unknown.status_code == 400
    assert "项目不存在" in unknown.json()["detail"]


def test_every_category_has_at_least_four_templates():
    from collections import Counter

    counts = Counter(x["category"] for x in e.public_templates())
    assert counts
    thin = {k: v for k, v in counts.items() if v < 4}
    assert not thin, thin


def test_every_template_has_cover_and_sample_url():
    for tpl in e.public_templates():
        assert tpl["cover"], tpl["id"]
        assert tpl["sample_video_url"], tpl["id"]


def _synthetic_segment(text, start=0.0, end=4.0):
    chars = [[c, round(start + i * (end - start) / len(text), 3)] for i, c in enumerate(text)]
    return {"start": start, "end": end, "text": text, "chars": chars}


def test_caption_fit_splits_long_segment_and_keeps_timing():
    from backend.app.services import koubo_caption_fit as f

    text = "今天给大家介绍一款性价比特别高的降噪耳机，原价三百九十九，现在只要一百九十九"
    out = f.fit_segments([_synthetic_segment(text)], width=270, height=480, caption_size=0.05, max_chars=22)
    assert len(out) >= 4
    assert out[0]["start"] == 0.0
    assert out[-1]["end"] == 4.0
    for left, right in zip(out, out[1:]):
        assert left["end"] <= right["start"] + 0.001
    for seg in out:
        assert seg["chars"]
        assert len(seg["text"].replace(" ", "")) <= 22


def test_caption_keywords_prioritize_numbers():
    from backend.app.services import koubo_caption_fit as f

    text = "今天给大家介绍一款性价比特别高的降噪耳机，原价三百九十九，现在只要一百九十九"
    seg = _synthetic_segment(text)
    kw = f.extract_keywords([seg])
    words = [x["text"] for x in kw]
    assert any("三百九十九" in w for w in words)
    assert any("一百九十九" in w for w in words)
    assert all(x["start"] < x["end"] for x in kw)


def test_batch_cancel_marks_pending_items():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import skill_deps as s

    job_id = "batch-cancel-test"
    with s._RENDER_LOCK:
        s._BATCH_JOBS[job_id] = {
            "id": job_id, "status": "running", "progress": 10, "done": 1, "total": 3,
            "message": "running", "items": [
                {"project_id": "p1", "status": "done"},
                {"project_id": "p2", "status": "pending"},
                {"project_id": "p3", "status": "pending"},
            ], "template_id": "premium_kinetic_v1", "title": "", "quality": "preview",
        }
    app = FastAPI()
    app.include_router(s.router)
    client = TestClient(app)
    r = client.post("/api/skills/digital-human-koubo/batch-render/jobs/%s/cancel" % job_id)
    assert r.status_code == 200
    with s._RENDER_LOCK:
        job = s._BATCH_JOBS[job_id]
        assert job["cancel"] is True
        assert [x["status"] for x in job["items"]] == ["done", "skipped", "skipped"]


def test_batch_retry_restarts_only_failed_items(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import skill_deps as s

    job_id = "batch-retry-test"
    with s._RENDER_LOCK:
        s._BATCH_JOBS[job_id] = {
            "id": job_id, "status": "done", "progress": 100, "done": 2, "total": 2,
            "message": "done", "items": [
                {"project_id": "ok1", "status": "done"},
                {"project_id": "bad1", "status": "error"},
            ], "template_id": "clean_docu_v1", "title": "t", "quality": "final",
        }
    captured = {}

    def fake_start(ids, template_id, title, quality):
        captured.update({"ids": ids, "template_id": template_id, "title": title, "quality": quality})
        return {"ok": True, "job_id": "new-job", "total": len(ids)}

    monkeypatch.setattr(s, "_start_batch_render", fake_start)
    app = FastAPI()
    app.include_router(s.router)
    client = TestClient(app)
    r = client.post("/api/skills/digital-human-koubo/batch-render/jobs/%s/retry" % job_id)
    assert r.status_code == 200
    assert r.json()["job_id"] == "new-job"
    assert captured == {"ids": ["bad1"], "template_id": "clean_docu_v1", "title": "t", "quality": "final"}

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


def test_all_template_specs_pass_validation():
    issues = e.template_issues()
    assert issues == {}
    for tpl in e.public_templates():
        assert e.validate_spec(e.get_template(tpl["id"])) == []


def test_invalid_spec_reports_issues():
    issues = e.validate_spec({"id": "broken"})
    assert any("name" in x for x in issues)
    assert any("layout" in x for x in issues)
    assert any("style.accent" in x for x in issues)


def test_batch_render_runs_items_in_parallel(monkeypatch, tmp_path):
    import threading
    import time

    from backend.app.api import skill_deps as s

    monkeypatch.setenv("KOUBO_BATCH_WORKERS", "2")
    lock = threading.Lock()
    state = {"active": 0, "max": 0}

    def fake_render(job_id, *args, **kwargs):
        with lock:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        time.sleep(0.15)
        with lock:
            state["active"] -= 1
        job = s._RENDER_JOBS[job_id]
        job["status"] = "done"
        job["progress"] = 100
        job["message"] = "done"
        job["qa"] = {"ok": True, "score": 90}

    monkeypatch.setattr(s, "_render_job", fake_render)
    items = [{"project_id": "p%d" % i, "name": "p%d" % i, "status": "pending",
              "progress": 0, "message": "", "output": "", "score": None, "ok": False, "error": ""}
             for i in range(4)]
    batch_id = "batch-parallel-test"
    with s._RENDER_LOCK:
        s._BATCH_JOBS[batch_id] = {"id": batch_id, "status": "running", "progress": 0, "done": 0,
                                   "total": 4, "items": items, "template_id": "premium_kinetic_v1",
                                   "title": "", "quality": "preview"}
    s._batch_render_job(batch_id, ["p0", "p1", "p2", "p3"], "premium_kinetic_v1", "", "preview")
    with s._RENDER_LOCK:
        batch = s._BATCH_JOBS[batch_id]
    assert batch["status"] == "done"
    assert all(x["status"] == "done" for x in items)
    assert state["max"] >= 2


def test_bgm_upload_and_defaults(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import skill_deps as s

    monkeypatch.setattr(s, "BGM_DIR", tmp_path / "bgm")
    app = FastAPI()
    app.include_router(s.router)
    client = TestClient(app)
    r = client.post("/api/skills/digital-human-koubo/bgm-uploads",
                    files={"file": ("bgm.wav", b"RIFF0000WAVEfmt ", "audio/wav")})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["id"].endswith(".wav")
    assert data["url"].startswith("/api/skills/digital-human-koubo/bgm-uploads/")
    assert (tmp_path / "bgm" / data["id"]).is_file()
    cfg = s.BgmConfig()
    assert cfg.asset_id == "" and cfg.volume == 0.18 and cfg.fade == 1.0


def test_bgm_upload_rejects_non_audio(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import skill_deps as s

    monkeypatch.setattr(s, "BGM_DIR", tmp_path / "bgm")
    app = FastAPI()
    app.include_router(s.router)
    client = TestClient(app)
    r = client.post("/api/skills/digital-human-koubo/bgm-uploads",
                    files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_broll_suggest_matches_filename(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import skill_deps as s

    broll = tmp_path / "broll"
    broll.mkdir()
    (broll / "降噪耳机特写.jpg").write_bytes(b"x")
    (broll / "办公室空镜.mp4").write_bytes(b"x")
    monkeypatch.setattr(s, "BROLL_DIR", broll)
    monkeypatch.setattr(s, "_load_projects", lambda: [{
        "id": "p1", "script": "降噪耳机原价三百九十九，现在只要一百九十九",
        "segments": [{"text": "降噪耳机原价三百九十九", "seconds": 6}],
    }])
    app = FastAPI()
    app.include_router(s.router)
    client = TestClient(app)
    r = client.post("/api/skills/digital-human-koubo/broll-suggest", json={"project_id": "p1", "limit": 1})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["matched"] is True
    assert data["items"][0]["asset_id"] == "降噪耳机特写.jpg"
    assert "降噪" in data["items"][0]["reason"] or "耳机" in data["items"][0]["reason"]


def test_broll_suggest_returns_empty_without_match(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import skill_deps as s

    broll = tmp_path / "broll"
    broll.mkdir()
    (broll / "办公室空镜.mp4").write_bytes(b"x")
    monkeypatch.setattr(s, "BROLL_DIR", broll)
    monkeypatch.setattr(s, "_load_projects", lambda: [{
        "id": "p1", "script": "降噪耳机原价三百九十九", "segments": [{"text": "降噪耳机", "seconds": 6}],
    }])
    app = FastAPI()
    app.include_router(s.router)
    client = TestClient(app)
    r = client.post("/api/skills/digital-human-koubo/broll-suggest", json={"project_id": "p1"})
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_shot_bounds_uses_segment_starts():
    segs = [{"start": 0.0, "end": 2.0}, {"start": 2.0, "end": 5.0}, {"start": 5.0, "end": 8.0}]
    bounds = e._shot_bounds(segs, 8.0, 3)
    assert len(bounds) == 4
    assert bounds[0] == 0.0 and bounds[-1] == 8.0
    assert bounds == sorted(bounds)
    assert all(bounds[i + 1] - bounds[i] >= 0.8 for i in range(3))


def test_render_rejects_bad_shots(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import skill_deps as s

    (tmp_path / "p1").mkdir()
    (tmp_path / "p1" / "final.mp4").write_bytes(b"x")
    monkeypatch.setattr(s, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(s, "koubo_project", lambda pid: {"id": pid, "status": "generated"})
    app = FastAPI()
    app.include_router(s.router)
    client = TestClient(app)
    one = client.post("/api/skills/digital-human-koubo/projects/p1/render",
                      json={"template_id": "premium_kinetic_v1", "shots": [{"template_id": "premium_kinetic_v1"}]})
    assert one.status_code == 400
    bad = client.post("/api/skills/digital-human-koubo/projects/p1/render",
                      json={"shots": [{"template_id": "nope"}, {"template_id": "premium_kinetic_v1"}]})
    assert bad.status_code == 400
    assert "模板不存在" in bad.json()["detail"]


def test_uploads_list_excludes_variants(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import skill_deps as s

    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "a_9x16.jpg").write_bytes(b"x")
    (tmp_path / "note.txt").write_text("x")
    monkeypatch.setattr(s, "UPLOAD_DIR", tmp_path)
    app = FastAPI()
    app.include_router(s.router)
    client = TestClient(app)
    data = client.get("/api/skills/digital-human-koubo/uploads").json()
    assert [x["id"] for x in data["uploads"]] == ["a.jpg"]


def test_matrix_create_projects(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import skill_deps as s

    saved = {"rows": []}
    monkeypatch.setattr(s, "_load_projects", lambda: saved["rows"])
    monkeypatch.setattr(s, "_save_projects", lambda rows: saved.update(rows=rows))
    app = FastAPI()
    app.include_router(s.router)
    client = TestClient(app)
    r = client.post("/api/skills/digital-human-koubo/projects/batch", json={
        "scripts": ["第一条文案", "第二条文案"],
        "photo_ids": ["a.jpg", "b.jpg"],
        "options": {"shot": "full", "host": "photo", "resolution": "720p", "language": "Mandarin Chinese"},
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count"] == 4
    assert data["total_credits"] > 0
    assert len(saved["rows"]) == 4


def test_batch_generate_happy_path(monkeypatch):
    import time

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import skill_deps as s

    monkeypatch.setattr(s, "_load_projects", lambda: [{
        "id": "p1", "name": "n1", "status": "ready", "total_credits": 100,
        "segments": [{"text": "文案", "seconds": 6}],
    }])

    def fake_gen(job_id, pid, token, first_only, installation_id):
        job = s._GEN[job_id]
        job["status"] = "done"
        job["progress"] = 100

    monkeypatch.setattr(s, "_gen_job", fake_gen)
    app = FastAPI()
    app.include_router(s.router)
    client = TestClient(app)
    r = client.post("/api/skills/digital-human-koubo/batch-generate",
                    json={"project_ids": ["p1"], "all": True},
                    headers={"Authorization": "Bearer test"})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    data = {}
    for _ in range(40):
        data = client.get("/api/skills/digital-human-koubo/batch-generate/jobs/%s" % job_id).json()
        if data["status"] != "running":
            break
        time.sleep(0.1)
    assert data["status"] == "done"
    assert data["items"][0]["status"] == "done"
    assert data["total_credits"] == 100


def test_batch_generate_cancel_marks_pending():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import skill_deps as s

    job_id = "gen-cancel-test"
    with s._GEN_LOCK:
        s._BATCH_GEN[job_id] = {
            "id": job_id, "status": "running", "items": [
                {"project_id": "p1", "status": "done"},
                {"project_id": "p2", "status": "pending"},
            ],
        }
    app = FastAPI()
    app.include_router(s.router)
    client = TestClient(app)
    r = client.post("/api/skills/digital-human-koubo/batch-generate/jobs/%s/cancel" % job_id)
    assert r.status_code == 200
    with s._GEN_LOCK:
        assert [x["status"] for x in s._BATCH_GEN[job_id]["items"]] == ["done", "skipped"]


def test_should_trim_duration():
    from backend.app.api import skill_deps as s

    assert s._should_trim(8.04, 6) is False
    assert s._should_trim(22.0, 9) is True
    assert s._should_trim(0, 9) is False
    assert s._should_trim(9, 0) is False


def test_trim_media_shortens_video(tmp_path, monkeypatch):
    import subprocess

    from backend.app.api import skill_deps as s

    ffmpeg = s.FFMPEG_DIR / "ffmpeg.exe"
    if not ffmpeg.is_file():
        return
    src = tmp_path / "long.mp4"
    subprocess.run([str(ffmpeg), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=160x120:rate=12", "-t", "2", "-pix_fmt", "yuv420p", str(src)], check=True)
    assert s._trim_media(src, 1.0) is True
    assert abs(s._media_duration(src) - 1.0) < 0.25


def test_short_speech_keeps_whisper_timing(monkeypatch, tmp_path):
    from backend.app.services import koubo_renderer as kr

    text = "全身，边走路边介绍apec"
    chars = [[c, round(i * 4.12 / len(text), 3)] for i, c in enumerate(text)]
    monkeypatch.setattr(kr, "_whisper_chars", lambda *args, **kwargs: chars)
    project = {"segments": [{"text": text, "seconds": 6}], "options": {}}
    timeline = kr.build_timeline(project, 8.0, tmp_path, tmp_path / "v.mp4", "zh")
    assert timeline["timing"] == "whisper"
    assert timeline["segs"][-1]["end"] < 5.0

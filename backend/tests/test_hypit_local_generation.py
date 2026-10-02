import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app.api import hypit_local


def _project(tmp_path):
    project = tmp_path / "hypit-project"
    (project / "assets").mkdir(parents=True)
    (project / "assets" / "reference.mp4").write_bytes(b"local source")
    (project / "project.json").write_text(json.dumps({
        "job_id": "a" * 32,
        "source_name": "reference.mp4",
        "width": 1080,
        "height": 1920,
        "scenes": [{
            "start": 0, "end": 10, "caption": "字幕",
            "image_prompt": "街头人物", "video_prompt": "缓慢前行",
        }],
    }), encoding="utf-8")
    hypit_local._write_generation(project, {"status": "queued", "scenes": [{}]})
    return project


@pytest.mark.asyncio
async def test_online_image_and_wan_video_then_local_hypit(tmp_path, monkeypatch):
    project = _project(tmp_path)
    requests = []

    def frame(_, destination, __):
        destination.write_bytes(b"jpeg")

    async def server(_, method, path, __, **kwargs):
        requests.append((method, path, kwargs))
        if path.endswith("/edits/start"):
            return {"job_id": "image-1"}
        if path.endswith("/images/jobs/image-1"):
            return {"status": "completed", "result": {"data": [{"url": "https://media.example/image.png"}]}}
        if method == "POST":
            return {"output": {"task_id": "wan-1"}}
        return {"status": "completed", "url": "https://media.example/video.mp4"}

    async def download(_, url, destination, __):
        destination.write_bytes(url.encode())

    def hypit(_, __, args, ___, timeout=300):
        if args[0] == "check":
            return {"ok": True}
        return {"build": {"id": "build-test-1234"}}

    monkeypatch.setattr(hypit_local, "_extract_frame", frame)
    monkeypatch.setattr(hypit_local, "_server_json", server)
    monkeypatch.setattr(hypit_local, "_download_generated", download)
    monkeypatch.setattr(hypit_local, "_hypit_root", lambda: tmp_path)
    monkeypatch.setattr(hypit_local, "_node_executable", lambda: "node")
    monkeypatch.setattr(hypit_local, "_run_hypit_json", hypit)

    await hypit_local._generate_hypit_media(project, "test-token", "test-installation")

    state = hypit_local._read_generation(project)
    assert state["status"] == "rendering"
    assert state["scenes"][0]["image_job_id"] == "image-1"
    assert state["scenes"][0]["video_task_id"] == "wan-1"
    video_body = next(args["json"] for method, _, args in requests if method == "POST" and "json" in args)
    assert video_body["image_url"] == "https://media.example/image.png"
    assert video_body["duration"] == 10
    assert 'src="./assets/take-01.mp4"' in (project / "main.svml").read_text(encoding="utf-8")
    assert sum(method == "POST" for method, _, _ in requests) == 2


@pytest.mark.asyncio
async def test_uncertain_video_submit_never_retries_paid_request(tmp_path, monkeypatch):
    project = _project(tmp_path)
    (project / "assets" / "image-01.png").write_bytes(b"image")
    hypit_local._write_generation(project, {
        "status": "queued",
        "scenes": [{"image_url": "https://media.example/image.png"}],
    })
    requests = []

    def frame(_, destination, __):
        destination.write_bytes(b"jpeg")

    async def server(_, method, path, __, **kwargs):
        requests.append((method, path))
        raise OSError("connection lost after upload")

    monkeypatch.setattr(hypit_local, "_extract_frame", frame)
    monkeypatch.setattr(hypit_local, "_server_json", server)

    await hypit_local._generate_hypit_media(project, "token", "installation")
    await hypit_local._generate_hypit_media(project, "token", "installation")

    assert len(requests) == 1
    state = hypit_local._read_generation(project)
    assert state["scenes"][0]["video_submit_uncertain"] is True
    assert "重复扣费" in state["error"]


def test_image_ratio_and_video_poll_response():
    assert hypit_local._image_size("1:1") == "1024x1024"
    assert hypit_local._video_poll_result({"status": "completed", "url": "https://media.example/video.mp4"}) == (
        "completed", "https://media.example/video.mp4", ""
    )


def test_speech_cues_use_recorded_timing_not_guessed_storyboard():
    text, cues = hypit_local._speech_cues({
        "stt_data": {"output": {
            "text": "你好世界",
            "utterances": [{
                "text": "你好世界", "start_time": 200, "end_time": 2100,
                "words": [
                    {"text": "你好", "start_time": 200, "end_time": 900},
                    {"text": "世界", "start_time": 1100, "end_time": 2100},
                ],
            }],
        }},
    }, 3)
    assert text == "你好世界"
    assert cues == [
        {"start": 0.2, "end": 2.1, "text": "你好世界"},
    ]


def test_speech_cues_break_at_sentence_and_pause():
    text, cues = hypit_local._speech_cues({"stt_data": {"output": {
        "utterances": [{"words": [
            {"text": "你好。", "start_time": 0, "end_time": 600},
            {"text": "世界", "start_time": 900, "end_time": 1600},
            {"text": "再见", "start_time": 2500, "end_time": 3000},
        ]}],
    }}}, 3)
    assert text == "你好。世界再见"
    assert cues == [
        {"start": 0, "end": 0.6, "text": "你好。"},
        {"start": 0.9, "end": 1.6, "text": "世界"},
        {"start": 2.5, "end": 3, "text": "再见"},
    ]


def test_short_video_analysis_uses_fewer_frames_and_scenes():
    assert hypit_local._analysis_frame_count(7) == 4
    assert hypit_local._suggested_scene_count(7) == 1
    assert hypit_local._generation_boundaries(10) == [(0.0, 10.0)]
    assert hypit_local._wan_generation_duration(*hypit_local._generation_boundaries(10)[0]) == 10
    assert len(hypit_local._generation_boundaries(31)) == 2
    assert min(end - start for start, end in hypit_local._generation_boundaries(31)) >= 5
    assert len(hypit_local._generation_boundaries(7.23)) == 1
    assert hypit_local._wan_generation_duration(*hypit_local._generation_boundaries(7.23)[0]) == 8
    assert hypit_local._analysis_frame_count(60) == 12
    assert hypit_local._suggested_scene_count(60) == 2
    assert hypit_local._suggested_scene_count(60) == len(hypit_local._generation_boundaries(60))


def test_timeline_time_uses_exact_frame_boundaries():
    assert hypit_local._timeline_time(7.233) == "217f"
    assert hypit_local._timeline_time(0.2) == "6f"


@pytest.mark.asyncio
async def test_build_requires_recheck_after_transcription(tmp_path, monkeypatch):
    project = _project(tmp_path)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "speech.json").write_text(
        json.dumps({"text": "真实口播", "cues": [{"start": 0, "end": 1, "text": "真实口播"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(hypit_local, "_project_dir", lambda *args: project)
    monkeypatch.setattr(hypit_local, "_read_job", lambda *args: (job_dir, {}))
    with pytest.raises(HTTPException, match="口播字幕已更新") as error:
        await hypit_local.build_local_hypit_project(
            "a" * 32, hypit_local.BuildIn(confirmed_external_generation=True),
            None, SimpleNamespace(id=23),
        )
    assert error.value.status_code == 409


def test_reference_audio_and_timed_captions_in_hypit_project(tmp_path):
    project = _project(tmp_path)
    scenes = [{"start": 0, "end": 8, "caption": "AI 猜的文字"}]
    hypit_local._write_project_sources(
        project, "reference.mp4", 1080, 1920, scenes, has_audio=True,
        speech_cues=[{"start": 0.2, "end": 1.5, "text": "真实口播"}],
    )
    graph = (project / "main.svml").read_text(encoding="utf-8")
    assert 'audio="default"' in graph
    assert '<audio-track:Track id="reference-sound"' in graph
    assert '<film:Track source={reference-sound.audio}/>' in graph
    assert 'start="6f" end="45f"' in graph
    assert "真实口播" in graph
    assert "AI 猜的文字" not in graph
    hypit_local._write_project_sources(project, "reference.mp4", 1080, 1920, scenes, has_audio=True)
    assert "AI 猜的文字" not in (project / "main.svml").read_text(encoding="utf-8")


def test_project_without_transcript_omits_empty_caption_track(tmp_path):
    project = _project(tmp_path)
    scenes = [{"start": 0, "end": 8, "caption": "不要臆造字幕"}]
    hypit_local._write_project_sources(project, "reference.mp4", 1080, 1920, scenes, has_audio=True)
    graph = (project / "main.svml").read_text(encoding="utf-8")
    assert '<typo:Track id="captions"' not in graph
    assert "captions.track" not in graph
    assert '<audio-track:Track id="reference-sound"' in graph
    assert "不要臆造字幕" not in graph


def test_configure_runtime_uses_installed_chrome_without_managed_download(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    runtime_path = project / "hypit.runtime.json"
    runtime_path.write_text(json.dumps({
        "endpoints": {
            "hyperframes.local": {
                "use": "@hypit/provider-hyperframes-local",
                "config": {"browserVersion": "152.0.7928.2"},
            },
        },
    }), encoding="utf-8")
    chrome = tmp_path / "chrome.exe"
    chrome.write_bytes(b"chrome")
    monkeypatch.setattr(hypit_local, "_installed_chrome_path", lambda: chrome)

    hypit_local._configure_runtime_browser(project)

    config = json.loads(runtime_path.read_text(encoding="utf-8"))["endpoints"]["hyperframes.local"]["config"]
    assert config == {"chromePath": chrome.as_posix()}


def test_hypit_environment_uses_shared_state_and_windows_system_tools(monkeypatch):
    monkeypatch.setattr(hypit_local.os, "name", "nt")
    monkeypatch.setattr(hypit_local, "_hypit_state_home", lambda: Path(r"C:\Users\tester\AppData\Local\Hypit"))
    monkeypatch.setenv("PATH", r"C:\Tools\node")
    monkeypatch.setenv("WINDIR", r"C:\Windows")

    env = hypit_local._hypit_env()

    assert env["HYPIT_STATE_HOME"] == r"C:\Users\tester\AppData\Local\Hypit"
    assert env["NODE_DISABLE_COMPILE_CACHE"] == "1"
    assert env["TSX_DISABLE_CACHE"] == "1"
    assert env["PATH"].startswith(r"C:\Windows\System32;")


def test_hypit_environment_prefers_bundled_npm(tmp_path, monkeypatch):
    """方案 A：包内 npm 优先（用户机器可能根本没装 Node），系统 npm 只做兜底。"""
    node_dir = tmp_path / "nodejs"
    node_dir.mkdir()
    (node_dir / "npm.cmd").write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(hypit_local, "ROOT", tmp_path)
    monkeypatch.setattr(hypit_local.os, "name", "nt")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "programfiles"))
    monkeypatch.setenv("PATH", r"D:\\lobster\\nodejs;" + str(node_dir) + r";C:\\Windows\\System32")

    env = hypit_local._hypit_env()

    assert env["PATH"].split(";")[0].lower() == str(node_dir).lower()
    assert hypit_local._npm_executable() == str(node_dir / "npm.cmd")


def test_installed_hypit_package_is_verified_by_name_and_version(tmp_path, monkeypatch):
    package = (
        tmp_path / "packages" / "@hyperframes" / "engine" / "0.7.101"
        / "node_modules" / "@hyperframes" / "engine"
    )
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({
        "name": "@hyperframes/engine",
        "version": "0.7.101",
    }), encoding="utf-8")
    monkeypatch.setattr(hypit_local, "_hypit_state_home", lambda: tmp_path)

    assert hypit_local._installed_hypit_registry_package("@hyperframes/engine", "0.7.101")
    assert not hypit_local._installed_hypit_registry_package("@hyperframes/engine", "0.7.102")


def test_nonzero_npm_install_is_recoverable_only_when_exact_engine_is_installed(tmp_path, monkeypatch):
    package = (
        tmp_path / "packages" / "@hyperframes" / "engine" / "0.7.101"
        / "node_modules" / "@hyperframes" / "engine"
    )
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({
        "name": "@hyperframes/engine",
        "version": "0.7.101",
    }), encoding="utf-8")
    monkeypatch.setattr(hypit_local, "_hypit_state_home", lambda: tmp_path)

    assert hypit_local._has_recoverable_hypit_install_error(
        RuntimeError("Installing @hyperframes/engine@0.7.101 · log C:\\Temp\\install.log")
    )
    assert not hypit_local._has_recoverable_hypit_install_error(
        RuntimeError("npm install failed")
    )


@pytest.mark.asyncio
async def test_runtime_prepare_retries_shared_install_race(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    calls = []
    monkeypatch.setattr(hypit_local, "_hypit_state_home", lambda: tmp_path / "state")

    def run_hypit(_, __, args, ___):
        calls.append(("run", args))
        (project / ".hypit").mkdir(exist_ok=True)
        (project / ".hypit" / "runtime").write_text("hypit.runtime.json", encoding="utf-8")
        return ""

    def configure(_):
        calls.append(("configure",))

    def run_json(_, __, args, ___, timeout=300):
        calls.append(("json", args, timeout))
        if len([item for item in calls if item[0] == "json"]) == 1:
            package = (tmp_path / "state" / "packages" / "@hyperframes" / "engine" / "0.7.101"
                       / "node_modules" / "@hyperframes" / "engine")
            package.mkdir(parents=True)
            (package / "package.json").write_text(json.dumps({
                "name": "@hyperframes/engine", "version": "0.7.101",
            }), encoding="utf-8")
            raise RuntimeError(r"Installing @hyperframes/engine@0.7.101 · log C:\Temp\install.log")
        return {"ready": True, "worker": "running"}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(hypit_local, "_run_hypit", run_hypit)
    monkeypatch.setattr(hypit_local, "_configure_runtime_browser", configure)
    monkeypatch.setattr(hypit_local, "_run_hypit_json", run_json)
    monkeypatch.setattr(hypit_local.asyncio, "sleep", no_sleep)

    output = await hypit_local._ensure_hypit_runtime_ready(project, tmp_path, "node")

    assert output["ready"] is True
    assert [item[1] for item in calls if item[0] == "json"] == [
        ["runtime", "up"],
        ["runtime", "up"],
    ]
    assert calls.count(("configure",)) == 1


@pytest.mark.parametrize("reply", [
    '```json\n{"title":"复刻","scenes":[{"start":0,"end":5,"caption":"街头",},]}\n```',
    '{"title":"复刻","scenes":[{"start":0,"end":5,"caption":"关于"街头"的视频"}]}',
])
def test_storyboard_parser_repairs_minor_model_json_errors(reply):
    parsed = hypit_local._extract_json_object(reply)
    scenes = hypit_local._normalize_scenes(parsed, 5)
    assert parsed["title"] == "复刻"
    assert len(scenes) == 1
    assert scenes[0]["end"] == 5


def test_storyboard_parser_rejects_non_storyboard_text():
    with pytest.raises(ValueError, match="JSON 分镜"):
        hypit_local._extract_json_object("抱歉，无法分析该视频")
    with pytest.raises(ValueError, match="分镜列表"):
        hypit_local._normalize_scenes(hypit_local._extract_json_object('{"title":"空"}'), 5)


def test_job_recovery_reports_only_persisted_stages(tmp_path, monkeypatch):
    monkeypatch.setattr(hypit_local, "JOBS_ROOT", tmp_path)
    job_id = "a" * 32
    job_dir = tmp_path / "23" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(
        json.dumps({"job_id": job_id, "user_id": 23, "filename": "source.mp4"}),
        encoding="utf-8",
    )
    user = SimpleNamespace(id=23)
    initial = hypit_local.get_local_hypit_job(job_id, user)
    assert initial["project_checked"] is False
    assert initial["runtime_prepared"] is False
    assert initial["has_generation"] is False
    assert initial["has_build"] is False
    assert initial["contact_sheet_url"].endswith(job_id + "/contact-sheet")

    project = job_dir / "hypit-project"
    (project / ".hypit").mkdir(parents=True)
    (project / "project.json").write_text(json.dumps({"scenes": [{}]}), encoding="utf-8")
    (project / ".hypit" / "runtime").write_text("local", encoding="utf-8")
    (project / "generation.json").write_text("{}", encoding="utf-8")
    restored = hypit_local.get_local_hypit_job(job_id, user)
    assert restored["project_checked"] is True
    assert restored["runtime_prepared"] is False
    assert restored["has_generation"] is True
    assert restored["has_build"] is False


def test_runtime_dependencies_report_missing_items(monkeypatch):
    monkeypatch.setattr(hypit_local, "_hypit_root", lambda: None)
    monkeypatch.setattr(hypit_local, "_node_executable", lambda: None)
    monkeypatch.setattr(hypit_local, "_npm_executable", lambda: None)
    monkeypatch.setattr(hypit_local, "_installed_chrome_path", lambda: None)
    monkeypatch.setattr(hypit_local, "_installed_hypit_registry_package", lambda *a, **k: False)
    monkeypatch.setattr(hypit_local, "_ffmpeg_executable", lambda: None)
    monkeypatch.setattr(hypit_local, "_ffprobe_executable", lambda: None)
    monkeypatch.setattr(hypit_local, "_uv_executable", lambda: None)   # 本机装了 uv 也要按"缺失"算

    items = hypit_local._runtime_dependencies()
    assert [item["key"] for item in items] == [
        "node", "npm", "chrome", "hypit", "hypit_deps", "uv", "ffmpeg", "ffprobe", "engine"]
    assert all(item["ok"] is False for item in items)


def test_runtime_install_records_progress_and_completes(tmp_path, monkeypatch):
    monkeypatch.setattr(hypit_local, "RUNTIME_STATE_PATH", tmp_path / "runtime_install.json")
    monkeypatch.setattr(hypit_local, "RUNTIME_WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(hypit_local, "_hypit_root", lambda: tmp_path / "hypit")
    monkeypatch.setattr(hypit_local, "_node_executable", lambda: "node")
    monkeypatch.setattr(hypit_local, "_npm_executable", lambda: "npm")
    monkeypatch.setattr(hypit_local, "_installed_chrome_path", lambda: tmp_path / "chrome.exe")
    monkeypatch.setattr(hypit_local, "_configure_runtime_browser", lambda project_dir: None)
    monkeypatch.setattr(hypit_local, "_dependency_status", lambda: {"ready": True})
    monkeypatch.setattr(hypit_local, "_installed_hypit_registry_package", lambda *a, **k: True)
    # 方案 A：安装流程会先确保发行包/运行依赖/uv（这里 stub 掉，只验证主流程）
    monkeypatch.setattr(hypit_local, "_ensure_hypit_distribution", lambda: tmp_path / "hypit")
    monkeypatch.setattr(hypit_local, "_install_hypit_node_modules", lambda dist_dir: None)
    monkeypatch.setattr(hypit_local, "_ensure_uv", lambda: "uv")

    calls = []

    def fake_stream(root, node, args, cwd):
        calls.append(list(args[:2]))
        hypit_local._append_runtime_log("installing " + " ".join(args[:2]))

    monkeypatch.setattr(hypit_local, "_stream_hypit_command", fake_stream)

    asyncio.run(hypit_local._run_runtime_install())

    state = hypit_local._runtime_state()
    assert calls == [["runtime", "init"], ["runtime", "up"]]
    assert state["status"] == "completed"
    assert int(state["percent"]) == 100
    assert any("runtime up" in line for line in state["log"])


def test_runtime_install_reports_dist_download_failure(tmp_path, monkeypatch):
    """方案 A：发行包不再要求用户手放；下载失败要把原因写进安装状态。"""
    monkeypatch.setattr(hypit_local, "RUNTIME_STATE_PATH", tmp_path / "runtime_install.json")
    monkeypatch.setattr(hypit_local, "_hypit_root", lambda: None)
    monkeypatch.setattr(hypit_local, "_node_executable", lambda: "node")
    monkeypatch.setattr(hypit_local, "_npm_executable", lambda: "npm")
    monkeypatch.setattr(hypit_local, "_installed_chrome_path", lambda: tmp_path / "chrome.exe")

    def boom():
        raise RuntimeError("下载 Hypit 发行包失败：全部镜像不可用")

    monkeypatch.setattr(hypit_local, "_ensure_hypit_distribution", boom)

    asyncio.run(hypit_local._run_runtime_install())

    state = hypit_local._runtime_state()
    assert state["status"] == "failed"
    assert "发行包" in state["error"]


def test_ffmpeg_prefers_bundled_copy(tmp_path, monkeypatch):
    """客户端不把 deps/ffmpeg 加 PATH，视频复刻必须优先用包内那份。"""
    bundled = tmp_path / "deps" / "ffmpeg"
    bundled.mkdir(parents=True)
    (bundled / "ffmpeg.exe").write_bytes(b"bin")
    (bundled / "ffprobe.exe").write_bytes(b"bin")
    monkeypatch.setattr(hypit_local, "_bundled_ffmpeg_dir", lambda: bundled)

    assert hypit_local._ffmpeg_executable() == str(bundled / "ffmpeg.exe")
    assert hypit_local._ffprobe_executable() == str(bundled / "ffprobe.exe")

    deps = {item["key"]: item["ok"] for item in hypit_local._runtime_dependencies()}
    assert deps["ffmpeg"] is True and deps["ffprobe"] is True


def test_hypit_env_skips_puppeteer_browser_download(tmp_path, monkeypatch):
    """用系统 Chrome：装依赖时不能去下一份 ~300MB 的 Chromium。"""
    monkeypatch.setenv("PUPPETEER_SKIP_DOWNLOAD", "")
    env = hypit_local._hypit_env()
    assert env.get("PUPPETEER_SKIP_DOWNLOAD") == "1"
    assert env.get("PUPPETEER_SKIP_CHROMIUM_DOWNLOAD") == "1"


def test_hypit_root_accepts_runtime_inside_client_dir(tmp_path, monkeypatch):
    """OTA 把运行时解到客户端根目录时也要能找到。"""
    runtime = tmp_path / "aihypit" / "hypit"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "hypit.mjs").write_text("// cli", encoding="utf-8")
    monkeypatch.setattr(hypit_local, "ROOT", tmp_path)
    monkeypatch.delenv("HYPIT_ROOT", raising=False)

    assert hypit_local._hypit_root() == runtime.resolve()


def test_workflow_params_reads_capability_payload():
    """能力节点（数字人口播等）的参数直接在 plan.payload.payload：
    员工编辑器必须能回读，否则「口播来源」这种勾选保存后再次打开就没了。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "static" / "js" / "views" / "h5-employees.js").read_text(encoding="utf-8")
    assert "payload.capability_id" in src
    assert "script_sources" in src



def test_bundled_chromium_and_npm_preferred(tmp_path, monkeypatch):
    """渲染浏览器/包管理器都优先用客户端自带的（用户机器不一定装 Chrome/Node）。"""
    chrome = tmp_path / "browser_chromium" / "chromium-1208" / "chrome-win64" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b"chrome")
    npm = tmp_path / "nodejs" / "npm.cmd"
    npm.parent.mkdir(parents=True)
    npm.write_text("@echo off", encoding="utf-8")
    monkeypatch.setattr(hypit_local, "ROOT", tmp_path)

    assert hypit_local._bundled_chromium_path() == chrome
    # 自带 Chromium 也要探活通过才会被选中（跑不起来就跳过）
    monkeypatch.setattr(hypit_local, "_chrome_works", lambda path: Path(path) == chrome)
    assert hypit_local._installed_chrome_path() == chrome
    assert hypit_local._npm_executable() == str(npm)


def test_runtime_dependencies_reports_hypit_dist_and_uv(tmp_path, monkeypatch):
    """依赖体检要把「发行包 / 运行依赖 / uv」分别列出来，缺了要提示点按钮安装。"""
    monkeypatch.setattr(hypit_local, "ROOT", tmp_path)
    monkeypatch.setattr(hypit_local, "_hypit_root", lambda: None)
    monkeypatch.setattr(hypit_local, "_uv_executable", lambda: None)

    items = {item["key"]: item for item in hypit_local._runtime_dependencies()}
    assert items["hypit"]["ok"] is False and "安装运行依赖" in items["hypit"]["detail"]
    assert items["hypit_deps"]["ok"] is False
    assert items["uv"]["ok"] is False


def test_safe_extract_tar_blocks_path_traversal(tmp_path):
    import io
    import tarfile

    archive = tmp_path / "dist.tgz"
    with tarfile.open(archive, "w:gz") as tf:
        evil = tarfile.TarInfo("../evil.txt")
        evil.size = 4
        tf.addfile(evil, io.BytesIO(b"evil"))
        good = tarfile.TarInfo("bin/hypit.mjs")
        good.size = 4
        tf.addfile(good, io.BytesIO(b"okay"))

    dest = tmp_path / "out"
    hypit_local._safe_extract_tar(archive, dest)
    assert (dest / "bin" / "hypit.mjs").is_file()
    assert not (tmp_path / "evil.txt").exists()


def test_download_first_available_falls_back_to_next_source(tmp_path):
    """第一个源挂了要自动换下一个源（装依赖最常见的失败点）。"""
    import http.server
    import socketserver
    import threading

    payload = b"hypit-dist-bytes" * 100
    served = tmp_path / "served.tgz"
    served.write_bytes(payload)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(tmp_path), **kwargs)

        def log_message(self, *args):
            return

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as srv:
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        target = tmp_path / "out" / "dist.tgz"
        url = hypit_local._download_first_available(
            ["http://127.0.0.1:1/nope.tgz", "http://127.0.0.1:%d/served.tgz" % port],
            target,
            label="测试",
        )
        srv.shutdown()

    assert url.endswith("/served.tgz")
    assert target.read_bytes() == payload



def test_locate_hypit_dist_root_handles_npm_package_layout(tmp_path):
    """npm 包解包后是 package/ 子目录（真实 tgz 就是这样），必须能找到发行根。"""
    flat = tmp_path / "flat"
    (flat / "bin").mkdir(parents=True)
    (flat / "bin" / "hypit.mjs").write_text("x", encoding="utf-8")
    assert hypit_local._locate_hypit_dist_root(flat) == flat

    nested = tmp_path / "nested"
    (nested / "package" / "bin").mkdir(parents=True)
    (nested / "package" / "bin" / "hypit.mjs").write_text("x", encoding="utf-8")
    assert hypit_local._locate_hypit_dist_root(nested) == nested / "package"

    empty = tmp_path / "empty"
    empty.mkdir()
    assert hypit_local._locate_hypit_dist_root(empty) is None



def test_tool_mirror_order_prefers_cdn():
    """一键安装的下载源顺序：CDN → 我们域名 → npmmirror → 官方。"""
    urls = hypit_local._hypit_dist_urls()
    assert urls[0].startswith("https://lobster-online-assets-") and "assets/client-code/tools/hypit-" in urls[0]
    assert any("bhzn.top/client/client-code/tools" in u for u in urls)
    assert any("registry.npmmirror.com" in u for u in urls)
    assert any("registry.npmjs.org" in u for u in urls)

    uv_urls = hypit_local._uv_urls()
    assert uv_urls[0].startswith("https://lobster-online-assets-")
    assert any("github.com/astral-sh/uv" in u for u in uv_urls)



def test_sanitize_hypit_manifest_drops_pnpm_workspace_fields(tmp_path, monkeypatch):
    """上游 package.json 里的 workspace:* 会让 npm 报 EUNSUPPORTEDPROTOCOL，装之前必须清掉。"""
    import json as _json

    dist = tmp_path / "hypit"
    dist.mkdir()
    (dist / "package.json").write_text(_json.dumps({
        "name": "@hypit/hypit",
        "version": "0.2.17",
        "dependencies": {"tsx": "^4.0.0"},
        "devDependencies": {"@hypit/artifact": "workspace:*"},
        "workspaces": ["packages/*"],
        "scripts": {"prepare": "pnpm build", "postinstall": "node x.js"},
    }), encoding="utf-8")
    monkeypatch.setattr(hypit_local, "RUNTIME_STATE_PATH", tmp_path / "state.json")

    assert hypit_local._sanitize_hypit_dist_manifest(dist) is True
    data = _json.loads((dist / "package.json").read_text(encoding="utf-8"))
    assert "devDependencies" not in data and "workspaces" not in data
    assert "prepare" not in data["scripts"] and data["scripts"]["postinstall"] == "node x.js"
    assert data["dependencies"] == {"tsx": "^4.0.0"}


def test_install_hypit_node_modules_retries_without_scripts(tmp_path, monkeypatch):
    """第一次带 scripts 失败时，要自动带 --ignore-scripts 重试。"""
    import subprocess as _sp

    dist = tmp_path / "hypit"
    dist.mkdir()
    (dist / "package.json").write_text('{"name": "@hypit/hypit", "devDependencies": {"a": "workspace:*"}}', encoding="utf-8")
    monkeypatch.setattr(hypit_local, "RUNTIME_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(hypit_local, "_node_executable", lambda: "node")
    monkeypatch.setattr(hypit_local, "_npm_executable", lambda: "npm")
    monkeypatch.setattr(hypit_local, "_hypit_env", lambda: {})

    calls = []

    class _Proc:
        def __init__(self, code, out=""):
            self.returncode = code
            self.stdout = out
            self.stderr = ""

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if "--ignore-scripts" not in args:
            return _Proc(1, 'npm error code EUNSUPPORTEDPROTOCOL\nnpm error Unsupported URL Type "workspace:": workspace:*')
        (dist / "node_modules").mkdir(exist_ok=True)
        return _Proc(0)

    monkeypatch.setattr(_sp, "run", fake_run)
    hypit_local._install_hypit_node_modules(dist)

    assert any("--ignore-scripts" in args for args in calls)
    assert (dist / "node_modules").is_dir()



def test_installed_chrome_skips_broken_bundled_chromium(tmp_path, monkeypatch):
    """自带 Chromium 探活失败时（--version 跑不起来）要跳到下一个可用的浏览器。"""
    bundled = tmp_path / "browser_chromium" / "chromium-1208" / "chrome-win64" / "chrome.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"not-a-real-chrome")
    system = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    system.parent.mkdir(parents=True)
    system.write_bytes(b"real-chrome")
    monkeypatch.setattr(hypit_local, "ROOT", tmp_path)
    monkeypatch.setattr(hypit_local, "_CHROME_PROBE_CACHE", {})
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "x86"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    monkeypatch.setattr(hypit_local, "_chrome_works", lambda path: Path(path) == system)
    assert hypit_local._installed_chrome_path() == system

    monkeypatch.setattr(hypit_local, "_chrome_works", lambda path: False)
    assert hypit_local._installed_chrome_path() is None



def test_missing_hypit_package_is_parsed_from_error():
    """hypit 报 "hypit packages install <pkg>@<ver>" 时要能解析出来。"""
    detail = ('{"format": "hypit.cli-error@1", "ok": false, "error": {"code": "CLI_ERROR", '
              '"message": "@fontsource-variable/inter is needed by this authored font. '
              'Install it once with: hypit packages install @fontsource-variable/inter@5.3.0"}}')
    assert hypit_local._missing_hypit_package(detail) == ("@fontsource-variable/inter", "5.3.0")
    assert hypit_local._missing_hypit_package("普通报错") is None


def test_run_hypit_auto_installs_missing_package_and_retries(tmp_path, monkeypatch):
    """缺包时：自动跑 hypit packages install，然后重试原命令，用户不用手敲。"""
    import subprocess as _sp

    monkeypatch.setattr(hypit_local, "RUNTIME_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(hypit_local, "_hypit_env", lambda: {})
    calls = []

    class _Proc:
        def __init__(self, code, out="", err=""):
            self.returncode = code
            self.stdout = out
            self.stderr = err

    error_json = ('{"format":"hypit.cli-error@1","ok":false,"error":{"code":"CLI_ERROR","message":'
                  '"@fontsource-variable/inter is needed by this authored font. Install it once with: '
                  'hypit packages install @fontsource-variable/inter@5.3.0"}}')

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[2] == "packages":
            return _Proc(0, "installed")
        if len([c for c in calls if c[2] != "packages"]) == 1:
            return _Proc(1, "", error_json)
        return _Proc(0, "build-ok")

    monkeypatch.setattr(_sp, "run", fake_run)
    out = hypit_local._run_hypit(tmp_path, "node", ["build", "--workspace", str(tmp_path)], 60)

    assert out == "build-ok"
    install_calls = [c for c in calls if c[2] == "packages"]
    assert install_calls and install_calls[0][3] == "install" and install_calls[0][4] == "@fontsource-variable/inter@5.3.0"



def test_finished_video_gets_public_url(tmp_path, monkeypatch):
    """出片后 video_url 必须是公网 https 地址（不能给内网 127.0.0.1），本机地址单独留一份。"""
    import asyncio as _asyncio

    from backend.app.api import hypit_local as HL

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "job.json").write_text('{"job_id": "j1"}', encoding="utf-8")
    video = job_dir / "final.video.mp4"
    video.write_bytes(b"mp4-bytes")

    written = {}

    class _Workflow(dict):
        def update(self, **kwargs):
            super().update(kwargs)
            return self

    workflow = _Workflow(status="running", stage="rendering", video_url="")

    async def fake_upload(request, user, path):
        return "https://cdn.example.com/final.mp4"

    monkeypatch.setattr(HL, "_upload_local_result", fake_upload)
    _asyncio.run(HL._finish_workflow_with_result(job_dir, workflow, video, request=None, current_user=None, job_id="j1"))

    assert workflow["video_url"] == "https://cdn.example.com/final.mp4"
    assert workflow["video_local_url"] == "/api/local/hypit/jobs/j1/workflow/video"
    assert workflow["video_public_url"] == "https://cdn.example.com/final.mp4"


def test_finished_video_falls_back_to_local_url_when_upload_fails(tmp_path, monkeypatch):
    import asyncio as _asyncio

    from backend.app.api import hypit_local as HL

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    video = job_dir / "final.video.mp4"
    video.write_bytes(b"mp4-bytes")

    class _Workflow(dict):
        def update(self, **kwargs):
            super().update(kwargs)
            return self

    async def boom(request, user, path):
        raise RuntimeError("线上服务没有返回可用的成片链接")

    monkeypatch.setattr(HL, "_upload_local_result", boom)
    workflow = _Workflow(status="running")
    _asyncio.run(HL._finish_workflow_with_result(job_dir, workflow, video, request=None, current_user=None, job_id="j2"))

    assert workflow["video_url"] == "/api/local/hypit/jobs/j2/workflow/video"
    assert workflow["video_public_url"] == ""



def test_kill_hypit_browsers_only_matches_hypit_markers(monkeypatch, tmp_path):
    """收尾清理只杀命令行带 hypit 目录特征的 Chrome，不碰用户自己的浏览器。"""
    import json as _json
    import subprocess as _sp

    state_home = tmp_path / "Hypit"
    monkeypatch.setattr(hypit_local, "_hypit_state_home", lambda: state_home)
    project = tmp_path / "proj"
    rows = [
        {"ProcessId": 111, "CommandLine": "chrome.exe --user-data-dir=%s\\profiles\\r1" % state_home},
        {"ProcessId": 222, "CommandLine": "chrome.exe --user-data-dir=C:\\Users\\a\\Documents\\browse https://baidu.com"},
        {"ProcessId": 333, "CommandLine": "chrome.exe --user-data-dir=%s\\.hypit\\runtimes\\local\\chrome" % project},
    ]
    kills = []

    class _Proc:
        def __init__(self, out=""):
            self.stdout = out
            self.stderr = ""
            self.returncode = 0

    def fake_run(args, **kwargs):
        if isinstance(args, list) and args and args[0] == "taskkill":
            kills.append(args)
            return _Proc()
        return _Proc(_json.dumps(rows))

    monkeypatch.setattr(_sp, "run", fake_run)
    killed = hypit_local._kill_hypit_browsers(str(project))

    assert killed == 2
    assert {call[2] for call in kills} == {"111", "333"}   # 用户自己的 222 不能动



def test_public_media_url_allows_proxy_fake_ip_domains(monkeypatch):
    """代理把域名解析成 fake-ip（198.18.x.x）时不能当成内网拒掉（用户 2026-10-02 的问题）。"""
    def fake_getaddrinfo(host, port, **kwargs):
        return [(2, 1, 6, "", ("198.18.3.79", 0))]

    monkeypatch.setattr(hypit_local.socket, "getaddrinfo", fake_getaddrinfo)
    url = "https://vip.edu888.top/media/x.png"
    assert hypit_local._public_media_url(url) == url

    # 域名解析到真正的私网才拦
    monkeypatch.setattr(hypit_local.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("192.168.1.9", 0))])
    try:
        hypit_local._public_media_url("https://internal.example.com/x.png")
        raise AssertionError("私网域名必须被拒绝")
    except RuntimeError as exc:
        assert "内网" in str(exc) and "192.168.1.9" in str(exc)


def test_public_media_url_blocks_literal_private_address():
    for url in ("https://127.0.0.1/x.png", "https://10.0.0.5/v.mp4", "https://[::1]/x.png"):
        try:
            hypit_local._public_media_url(url)
            raise AssertionError("字面私网地址必须被拒绝: " + url)
        except RuntimeError as exc:
            assert "内网" in str(exc)



def test_job_summary_and_history_list(tmp_path, monkeypatch):
    """历史记录：能看到状态/阶段/结果地址，能判断能不能继续。"""
    monkeypatch.setattr(hypit_local, "JOBS_ROOT", tmp_path)
    user_dir = tmp_path / "31"
    job_dir = user_dir / "j1"
    job_dir.mkdir(parents=True)
    (job_dir / "reference.mp4").write_bytes(b"video")
    (job_dir / "job.json").write_text(__import__("json").dumps({
        "job_id": "j1", "user_id": 31, "filename": "ref.mp4", "file_size": 10,
        "probe": {"duration": 7.2, "width": 720, "height": 1264}, "brief": "改成美食",
        "created_at": 1000.0,
    }), encoding="utf-8")
    (job_dir / "workflow.json").write_text(__import__("json").dumps({
        "status": "failed", "stage": "自动处理失败", "error": "xx", "video_url": "",
    }), encoding="utf-8")

    items = hypit_local._list_job_summaries(31)
    assert len(items) == 1
    item = items[0]
    assert item["job_id"] == "j1" and item["status"] == "failed"
    assert item["brief"] == "改成美食" and item["can_resume"] is True
    assert item["contact_sheet_url"].endswith("/j1/contact-sheet")
    assert hypit_local._list_job_summaries(99) == []


def test_saved_storyboard_reused_on_resume(tmp_path):
    """从历史继续时复用工程里已有的分镜，不重跑 AI。"""
    project = tmp_path / "hypit-project"
    project.mkdir()
    (project / "project.json").write_text(__import__("json").dumps({
        "title": "测试复刻", "scenes": [{"start": 0, "end": 3, "image_prompt": "p"}],
    }), encoding="utf-8")
    saved = hypit_local._saved_storyboard(project)
    assert saved and saved["cached"] is True and len(saved["scenes"]) == 1
    assert hypit_local._saved_storyboard(tmp_path / "nope") is None



def test_local_proxy_detection_prefers_env_and_ports(monkeypatch):
    """直连失败后要能找到本机代理：先看环境变量，再看常见端口。"""
    monkeypatch.setattr(hypit_local, "_PROXY_STATE", {"preferred": "", "checked": False})
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    assert hypit_local._local_proxy_url() == "http://127.0.0.1:7890"

    monkeypatch.setattr(hypit_local, "_PROXY_STATE", {"preferred": "", "checked": False})
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    for name in ("https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr(hypit_local, "_windows_system_proxy", lambda: "")

    def fake_connect(address, timeout=0.4):
        if address[1] == 7897:
            class _Sock:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

            return _Sock()
        raise OSError("closed")

    monkeypatch.setattr(hypit_local.socket, "create_connection", fake_connect)
    assert hypit_local._local_proxy_url() == "http://127.0.0.1:7897"


def test_windows_system_proxy_reads_registry_format(monkeypatch):
    """Clash 的系统代理写法（http=127.0.0.1:10808;https=...）要能解析出 https 那条。"""
    import winreg as _winreg

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_open(root, path):
        return _Key()

    def fake_query(key, name):
        return (1, 0) if name == "ProxyEnable" else ("http=127.0.0.1:10808;https=127.0.0.1:10808", 0)

    monkeypatch.setattr(_winreg, "OpenKey", fake_open)
    monkeypatch.setattr(_winreg, "QueryValueEx", fake_query)
    assert hypit_local._windows_system_proxy() == "http://127.0.0.1:10808"

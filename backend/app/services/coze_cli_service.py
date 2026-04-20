from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


ROOT_DIR = Path(__file__).resolve().parents[3]
RUNS_DIR = ROOT_DIR / "runs" / "workbench_cli"
SESSION_STATE_FILE = RUNS_DIR / "session_state.json"
HARDCODED_COZE_CLI_PAT = "sat_63YJw6MIvj9YG6nWozu2YHNZ8zOrgcNubOC0m2q0A89JSIifFGnvLzhF7r4Q84ef"


@dataclass
class CliHandleResult:
    ok: bool
    reply: str
    statuses: list[str] = field(default_factory=list)
    invoke_model: str = "coze-cli"
    meta: dict[str, Any] = field(default_factory=dict)


def _candidate_paths(name: str) -> list[str]:
    values: list[str] = []
    env_key = "COZE_CLI_PATH" if name == "coze" else "NPM_CLI_PATH"
    env_value = os.getenv(env_key)
    if env_value:
        values.append(str(env_value))
    found = shutil.which(name)
    if found:
        values.append(found)
    if os.name == "nt":
        if name == "coze":
            values.extend([
                r"D:\npm-global\coze.cmd",
                str(Path.home() / "AppData/Roaming/npm/coze.cmd"),
            ])
        elif name == "npm":
            values.extend([
                r"D:\application\node\npm.cmd",
                r"C:\Program Files\nodejs\npm.cmd",
            ])
    dedup: list[str] = []
    for item in values:
        if item and item not in dedup:
            dedup.append(item)
    return dedup


def _resolve_executable(name: str) -> Optional[str]:
    for candidate in _candidate_paths(name):
        if Path(candidate).exists():
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _run_command(
    executable: str,
    args: list[str],
    timeout: int = 120,
    cwd: Optional[Path] = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [executable, *args]
    return subprocess.run(
        cmd,
        cwd=str(cwd or ROOT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _spawn_detached(executable: str, args: list[str], cwd: Optional[Path] = None) -> None:
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        subprocess.Popen(  # noqa: S603
            [executable, *args],
            cwd=str(cwd or ROOT_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        return
    subprocess.Popen(  # noqa: S603
        [executable, *args],
        cwd=str(cwd or ROOT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _extract_json_block(text: str) -> Optional[Any]:
    raw = (text or "").strip()
    if not raw:
        return None
    for opener in ("{", "["):
        idx = raw.find(opener)
        if idx < 0:
            continue
        try:
            return json.loads(raw[idx:])
        except Exception:
            continue
    return None


def _contains_any(text: str, keywords: list[str]) -> bool:
    for item in keywords:
        if item and item in text:
            return True
    return False


def _looks_like_install_request(message: str) -> bool:
    text = (message or "").lower()
    return ("安装" in message and "cli" in text) or "@coze/cli" in text or "npm install -g @coze/cli" in text


def _looks_like_login_request(message: str) -> bool:
    text = (message or "").lower()
    return ("登录" in message or "授权" in message) and (
        "cli" in text or "云端账号" in message or "账号" in message
    )


def _looks_like_project_progress_request(message: str) -> bool:
    return _contains_any(
        message,
        ["继续", "继续处理", "继续完善", "查看进度", "项目进度", "查询进度", "查看结果", "结果", "进展"],
    )


def _looks_like_webpage_summary_request(message: str) -> bool:
    text = (message or "").strip()
    lower = text.lower()
    has_page_target = any(token in text for token in ["网页", "网站", "链接", "网址"]) or "url" in lower
    has_summary_intent = any(token in text for token in ["总结", "概括", "摘要", "分析", "提炼", "梳理"])
    return has_page_target and has_summary_intent


def _looks_like_web_app_request(message: str) -> bool:
    text = (message or "").strip()
    lower = text.lower()
    if not text:
        return False
    if _looks_like_webpage_summary_request(text):
        return False
    if any(token in text for token in ["网页应用", "网站应用", "落地页", "官网", "专题页"]):
        return True
    if "网站" in text:
        return True
    build_hints = ["做", "写", "建", "创建", "生成", "开发", "制作", "搭", "设计", "帮我", "给我"]
    if "网页" in text and any(token in text for token in build_hints):
        return True
    if "web" in lower and any(token in text for token in build_hints):
        return True
    return False


def _looks_like_mobile_app_request(message: str) -> bool:
    text = (message or "").strip()
    lower = text.lower()
    if any(token in text for token in ["移动应用", "手机应用", "安卓应用", "iOS应用"]):
        return True
    return "app" in lower and any(token in text for token in ["做", "写", "建", "创建", "生成", "开发", "制作", "帮我", "给我"])


def _clean_prompt_prefix(message: str) -> str:
    text = re.sub(r"^[#\s]+", "", (message or "").strip())
    text = re.sub(r"^(帮我|请|麻烦|直接|现在|立刻)+", "", text)
    return text.strip() or (message or "").strip()


def _read_session_state() -> dict[str, Any]:
    try:
        if not SESSION_STATE_FILE.exists():
            return {}
        return json.loads(SESSION_STATE_FILE.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}


def _write_session_state(data: dict[str, Any]) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_session_project(session_id: Optional[str]) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid:
        return {}
    data = _read_session_state()
    item = data.get(sid)
    return item if isinstance(item, dict) else {}


def save_session_project(
    session_id: Optional[str],
    *,
    project_id: str,
    project_type: str,
    project_name: str = "",
) -> None:
    sid = str(session_id or "").strip()
    pid = str(project_id or "").strip()
    if not sid or not pid:
        return
    data = _read_session_state()
    data[sid] = {
        "project_id": pid,
        "project_type": str(project_type or "").strip(),
        "project_name": str(project_name or "").strip(),
        "updated_at": int(time.time()),
    }
    _write_session_state(data)


def is_cli_installed() -> tuple[bool, Optional[str]]:
    exe = _resolve_executable("coze")
    return bool(exe), exe


def ensure_cli_installed() -> tuple[bool, str]:
    ok, exe = is_cli_installed()
    if ok and exe:
        return True, exe
    npm_exe = _resolve_executable("npm")
    if not npm_exe:
        return False, "未找到 npm，无法自动安装云端工作台 CLI。"
    proc = _run_command(npm_exe, ["install", "-g", "@coze/cli"], timeout=180)
    ok, exe = is_cli_installed()
    if proc.returncode == 0 and ok and exe:
        return True, exe
    output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    return False, output or "安装云端工作台 CLI 失败。"


def _run_cli_json(args: list[str], timeout: int = 180) -> tuple[bool, Any, str]:
    ok, exe = is_cli_installed()
    if not ok or not exe:
        return False, None, "未安装云端工作台 CLI"
    proc = _run_command(exe, ["--format", "json", *args], timeout=timeout)
    raw = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    payload = _extract_json_block(raw)
    if proc.returncode != 0:
        return False, payload, raw or "CLI 执行失败"
    return True, payload, raw


def get_auth_status() -> tuple[bool, dict[str, Any], str]:
    ok, exe = is_cli_installed()
    if not ok or not exe:
        return False, {"installed": False, "logged_in": False}, "未安装云端工作台 CLI"
    proc = _run_command(exe, ["--format", "json", "auth", "status"], timeout=30)
    payload = _extract_json_block((proc.stdout or "") + "\n" + (proc.stderr or "")) or {}
    logged_in = bool(payload.get("logged_in"))
    if proc.returncode != 0 and not payload:
        raw = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return False, {"installed": True, "logged_in": False}, raw or "暂时无法获取云端账号状态。"
    return True, {"installed": True, "logged_in": logged_in, **payload}, ""


def _configured_pat_token() -> str:
    return HARDCODED_COZE_CLI_PAT.strip()


def login_with_pat(token: str) -> tuple[bool, str]:
    pat = str(token or "").strip()
    if not pat:
        return False, "未提供云端工作台 PAT。"
    ok, exe = ensure_cli_installed()
    if not ok:
        return False, exe
    proc = _run_command(exe, ["--format", "json", "auth", "login", "--token", pat], timeout=60)
    payload = _extract_json_block((proc.stdout or "") + "\n" + (proc.stderr or "")) or {}
    if proc.returncode == 0:
        return True, str(payload.get("method") or "PAT")
    raw = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    return False, raw or "PAT 登录失败。"


def start_oauth_login() -> tuple[bool, str]:
    ok, exe = ensure_cli_installed()
    if not ok:
        return False, exe
    try:
        _spawn_detached(exe, ["auth", "login", "--oauth"])
        return True, "已经拉起云端账号授权流程，请在浏览器里完成登录。"
    except Exception as exc:
        return False, f"启动云端账号授权失败：{exc}"


def _latest_projects(size: int = 5) -> list[dict[str, Any]]:
    ok, payload, _raw = _run_cli_json(["code", "project", "list", "--size", str(size)], timeout=60)
    if not ok or not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _build_output_path(kind: str, suffix: str = "") -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    target = RUNS_DIR / f"{kind}_{stamp}"
    if suffix:
        target = target.with_suffix(suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not suffix:
        target.mkdir(parents=True, exist_ok=True)
    return target


def _current_project_status(project_id: str) -> tuple[bool, dict[str, Any], str]:
    ok, payload, raw = _run_cli_json(["code", "message", "status", "-p", project_id], timeout=60)
    if ok and isinstance(payload, dict):
        return True, payload, ""
    return False, payload if isinstance(payload, dict) else {}, raw


def _current_project_history(project_id: str) -> list[dict[str, Any]]:
    ok, payload, _raw = _run_cli_json(["code", "message", "history", "-p", project_id], timeout=60)
    if not ok or not isinstance(payload, dict):
        return []
    histories = payload.get("histories")
    return histories if isinstance(histories, list) else []


def _extract_project_answer(status_payload: dict[str, Any], project_id: str) -> str:
    answer = str(status_payload.get("answer") or "").strip()
    if answer:
        return answer
    for item in _current_project_history(project_id):
        if not isinstance(item, dict):
            continue
        candidate = str(item.get("answer") or "").strip()
        if candidate:
            return candidate
    return ""


def get_project_result_snapshot(project_id: str) -> tuple[bool, dict[str, Any], str]:
    ok, payload, raw = _current_project_status(project_id)
    if not ok:
        return False, payload, raw
    if not isinstance(payload, dict):
        return False, {}, raw
    answer = _extract_project_answer(payload, project_id)
    if answer:
        payload = {**payload, "answer": answer}
    return True, payload, ""


def get_project_preview_url(project_id: str) -> str:
    ok, payload, _raw = _run_cli_json(["code", "preview", project_id], timeout=180)
    if not ok or not isinstance(payload, dict):
        return ""
    return str(payload.get("preview_url") or "").strip()


def attach_real_preview_url(answer: str, project_id: str) -> str:
    text = str(answer or "").strip()
    if not text:
        return text
    preview_url = get_project_preview_url(project_id)
    if not preview_url:
        return text
    text = re.sub(r"https://[^\s)]+\.dev\.coze\.site", preview_url, text)
    if preview_url not in text:
        text += f"\n\n可直接查看地址：{preview_url}"
    return text


def build_project_progress_message(project_name: str, waited_seconds: int) -> str:
    name = str(project_name or "当前任务").strip()
    waited = max(int(waited_seconds or 0), 0)
    if waited <= 0:
        return f"{name} 已经提交到云端，正在开始处理。"
    if waited < 30:
        return f"{name} 正在云端持续处理中，已等待约 {waited} 秒。"
    if waited < 60:
        return f"{name} 还在云端处理中，已等待约 {waited} 秒，暂时还没有最终结果。"
    return f"{name} 处理时间比较长，已等待约 {waited} 秒。你可以先做别的事情，稍后再回来查看。"


def _poll_project_answer(
    project_id: str,
    *,
    timeout_seconds: int,
    interval_seconds: int = 4,
) -> tuple[bool, dict[str, Any], str]:
    deadline = time.time() + max(timeout_seconds, 1)
    last_payload: dict[str, Any] = {}
    while time.time() < deadline:
        ok, payload, _err = _current_project_status(project_id)
        if ok:
            last_payload = payload
            if str(payload.get("status") or "").lower() == "done":
                answer = _extract_project_answer(payload, project_id)
                return True, payload, answer
        time.sleep(interval_seconds)
    return False, last_payload, _extract_project_answer(last_payload, project_id) if last_payload else ""


def _latest_project_from_payload(payload: Any, latest: list[dict[str, Any]]) -> tuple[str, str]:
    payload_dict = payload if isinstance(payload, dict) else {}
    latest_project = latest[0] if latest else {}
    project_id = str(
        latest_project.get("id")
        or latest_project.get("project_id")
        or payload_dict.get("project_id")
        or payload_dict.get("id")
        or ""
    ).strip()
    project_name = str(
        latest_project.get("name")
        or payload_dict.get("name")
        or "新项目"
    ).strip()
    return project_id, project_name


def _project_processing_reply(project_ctx: dict[str, Any], status_payload: Optional[dict[str, Any]] = None) -> str:
    project_name = str(project_ctx.get("project_name") or "当前项目").strip()
    status = str((status_payload or {}).get("status") or "").strip().lower()
    reply = f"{project_name} 还在云端持续处理中。"
    if status == "processing":
        reply += " 我已经确认任务仍在执行，不是卡死，也不是断开。"
    reply += " 你可以继续留在当前对话里，我会后续继续查询处理结果。"
    return reply


def _send_project_message(project_id: str, message: str) -> tuple[bool, dict[str, Any], str]:
    ok, payload, raw = _run_cli_json(["code", "message", "send", message, "-p", project_id], timeout=120)
    if ok and isinstance(payload, dict):
        return True, payload, ""
    if isinstance(payload, dict):
        return False, payload, raw
    return False, {}, raw


def handle_cli_prompt(message: str, session_id: Optional[str] = None) -> CliHandleResult:
    text = (message or "").strip()
    if not text:
        return CliHandleResult(ok=False, reply="消息不能为空。")

    status_request = _contains_any(text, ["账号状态", "有没有登录", "是否登录", "登录了吗", "检测登录"])
    org_list_request = ("组织" in text) and _contains_any(text, ["列表", "查看", "有哪些"])
    space_list_request = (("空间" in text) or ("工作区" in text)) and _contains_any(text, ["列表", "查看", "有哪些"])
    create_web_request = _looks_like_web_app_request(text)
    create_app_request = _looks_like_mobile_app_request(text)
    generate_image_request = _contains_any(text, ["生成图片", "文生图", "出图", "画一张", "画几张", "图片生成"])
    generate_video_request = _contains_any(text, ["生成视频", "文生视频", "出视频", "视频生成"])
    generate_audio_request = _contains_any(text, ["生成音频", "生成语音", "配音", "tts", "语音合成"])
    progress_request = _looks_like_project_progress_request(text)
    session_project = get_session_project(session_id)
    default_project_type = "web"
    default_project_label = "网页应用"

    if _looks_like_install_request(text):
        ok, detail = ensure_cli_installed()
        if ok:
            return CliHandleResult(
                ok=True,
                statuses=["正在检查并安装云端工作台 CLI…"],
                reply="云端工作台 CLI 已安装完成。接下来你可以直接让我登录云端账号，或者开始创建网页应用。",
                meta={"action": "install_cli"},
            )
        return CliHandleResult(
            ok=False,
            statuses=["正在检查并安装云端工作台 CLI…"],
            reply=f"安装失败：{detail}",
            meta={"action": "install_cli"},
        )

    installed, cli_path = is_cli_installed()
    if not installed:
        ok, detail = ensure_cli_installed()
        if not ok:
            return CliHandleResult(
                ok=False,
                statuses=["正在自动安装云端工作台 CLI…"],
                reply=f"安装失败：{detail}",
                meta={"action": "install_cli"},
            )
        cli_path = detail

    status_ok, auth_payload, auth_error = get_auth_status()
    logged_in = bool(auth_payload.get("logged_in")) if status_ok else False
    configured_pat = _configured_pat_token()
    token_expires_at = str(auth_payload.get("token_expires_at") or "").strip()

    if configured_pat and (not logged_in or token_expires_at != "Never (PAT)"):
        pat_ok, _pat_detail = login_with_pat(configured_pat)
        status_ok, auth_payload, auth_error = get_auth_status()
        logged_in = bool(auth_payload.get("logged_in")) if status_ok else False
        token_expires_at = str(auth_payload.get("token_expires_at") or "").strip()
        if pat_ok:
            auth_payload = {**auth_payload, "auth_mode": "pat"}

    if status_request:
        if not status_ok:
            return CliHandleResult(
                ok=False,
                statuses=["正在检查云端账号状态…"],
                reply=auth_error or "暂时无法获取云端账号状态。",
                meta={"action": "auth_status"},
            )
        reply = (
            "云端账号已登录，可以继续创建网页应用、续聊项目会话或执行多媒体任务。"
            if logged_in
            else "云端账号当前未登录。你可以直接说“帮我登录云端账号”，我会拉起授权流程。"
        )
        return CliHandleResult(
            ok=True,
            statuses=["正在检查云端账号状态…"],
            reply=reply,
            meta={"action": "auth_status", "logged_in": logged_in, "cli_path": cli_path, "token_expires_at": token_expires_at},
        )

    if _looks_like_login_request(text) and configured_pat:
        return CliHandleResult(
            ok=True,
            statuses=["正在检查云端账号状态…"],
            reply="云端账号已使用长期密钥保持连接，可以直接继续发任务。",
            meta={"action": "auth_status", "logged_in": logged_in, "cli_path": cli_path, "token_expires_at": token_expires_at},
        )

    if _looks_like_login_request(text) or not logged_in:
        ok, detail = start_oauth_login()
        if ok:
            return CliHandleResult(
                ok=True,
                statuses=["正在启动云端账号授权…"],
                reply="我已经为你拉起云端账号登录授权了。请在浏览器里完成授权，完成后回复“继续”或者直接告诉我要做什么。",
                meta={"action": "auth_login"},
            )
        return CliHandleResult(
            ok=False,
            statuses=["正在启动云端账号授权…"],
            reply=detail,
            meta={"action": "auth_login"},
        )

    if org_list_request:
        ok, payload, raw = _run_cli_json(["organization", "list"], timeout=60)
        if not ok:
            return CliHandleResult(ok=False, statuses=["正在读取组织列表…"], reply=f"读取组织列表失败：{raw}", meta={"action": "organization_list"})
        return CliHandleResult(
            ok=True,
            statuses=["正在读取组织列表…"],
            reply=f"已拿到组织列表结果。\n{json.dumps(payload, ensure_ascii=False)[:3000]}",
            meta={"action": "organization_list", "result": payload},
        )

    if space_list_request:
        ok, payload, raw = _run_cli_json(["space", "list"], timeout=60)
        if not ok:
            return CliHandleResult(ok=False, statuses=["正在读取空间列表…"], reply=f"读取空间列表失败：{raw}", meta={"action": "space_list"})
        return CliHandleResult(
            ok=True,
            statuses=["正在读取空间列表…"],
            reply=f"已拿到空间列表结果。\n{json.dumps(payload, ensure_ascii=False)[:3000]}",
            meta={"action": "space_list", "result": payload},
        )

    if not session_project:
        prompt = _clean_prompt_prefix(text)
        project_type = default_project_type
        project_label = default_project_label
        ok, payload, raw = _run_cli_json(["code", "project", "create", "--message", prompt, "--type", project_type], timeout=120)
        if not ok:
            return CliHandleResult(
                ok=False,
                statuses=[f"正在创建{project_label}项目…"],
                reply=f"创建{project_label}失败：{raw}",
                meta={"action": f"create_{project_type}_project"},
            )
        latest = _latest_projects(3)
        project_id, project_name = _latest_project_from_payload(payload, latest)
        save_session_project(session_id, project_id=project_id, project_type=project_type, project_name=project_name)
        done, status_payload, answer = _poll_project_answer(project_id, timeout_seconds=12, interval_seconds=4)
        statuses = [f"正在创建{project_label}项目…", "正在连接这个项目的云端会话…"]
        if done and answer:
            reply = attach_real_preview_url(answer, project_id) if project_type == "web" else answer
            reply += f"\n\n这个{project_label}已经进入当前工作台流程。后续你直接继续提修改需求就行。"
            return CliHandleResult(
                ok=True,
                statuses=statuses,
                reply=reply,
                meta={"action": f"create_{project_type}_project", "result": payload, "project_id": project_id, "project_name": project_name},
            )
        reply = f"{project_label}已经提交到云端，正在开始处理。"
        return CliHandleResult(
            ok=True,
            statuses=statuses,
            reply=reply,
            meta={
                "action": f"create_{project_type}_project",
                "result": payload,
                "project_id": project_id,
                "project_name": project_name,
                "status": status_payload,
                "poll_project": True,
                "project_label": project_label,
            },
        )

    if session_project and progress_request:
        project_id = str(session_project.get("project_id") or "").strip()
        done, status_payload, answer = _poll_project_answer(project_id, timeout_seconds=15, interval_seconds=5)
        if done and answer:
            return CliHandleResult(
                ok=True,
                statuses=["正在查询当前项目会话状态…"],
                reply=attach_real_preview_url(answer, project_id),
                meta={"action": "project_status", "project_id": project_id, "status": status_payload},
            )
        return CliHandleResult(
            ok=True,
            statuses=["正在查询当前项目会话状态…"],
            reply=_project_processing_reply(session_project, status_payload),
            meta={"action": "project_status", "project_id": project_id, "status": status_payload, "project_name": session_project.get("project_name"), "poll_project": True},
        )

    if session_project:
        project_id = str(session_project.get("project_id") or "").strip()
        project_name = str(session_project.get("project_name") or "当前项目").strip()
        status_ok2, status_payload, status_err = _current_project_status(project_id)
        if status_ok2 and str(status_payload.get("status") or "").lower() == "processing":
            return CliHandleResult(
                ok=True,
                statuses=[f"正在连接 {project_name} 的项目会话…"],
                reply=_project_processing_reply(session_project, status_payload),
                meta={"action": "project_message_processing", "project_id": project_id, "status": status_payload, "project_name": project_name, "poll_project": True},
            )
        if not status_ok2 and status_err:
            return CliHandleResult(
                ok=False,
                statuses=[f"正在连接 {project_name} 的项目会话…"],
                reply=f"读取当前项目会话状态失败：{status_err}",
                meta={"action": "project_status_error", "project_id": project_id},
            )
        send_ok, send_payload, send_raw = _send_project_message(project_id, text)
        if not send_ok:
            if isinstance(send_payload, dict) and str(send_payload.get("desc") or "").strip() == "chat is processing, can't handle this":
                return CliHandleResult(
                    ok=True,
                    statuses=[f"正在把消息发送到 {project_name} 的项目会话…"],
                    reply=_project_processing_reply(session_project, status_payload),
                    meta={"action": "project_message_processing", "project_id": project_id, "status": status_payload, "project_name": project_name, "poll_project": True},
                )
            return CliHandleResult(
                ok=False,
                statuses=[f"正在把消息发送到 {project_name} 的项目会话…"],
                reply=f"发送项目消息失败：{send_raw}",
                meta={"action": "project_message_send", "project_id": project_id},
            )
        done, polled_payload, answer = _poll_project_answer(project_id, timeout_seconds=20, interval_seconds=4)
        if done and answer:
            return CliHandleResult(
                ok=True,
                statuses=[f"正在把消息发送到 {project_name} 的项目会话…", "正在等待云端项目返回结果…"],
                reply=attach_real_preview_url(answer, project_id),
                meta={"action": "project_message_send", "project_id": project_id, "send": send_payload, "status": polled_payload},
            )
        return CliHandleResult(
            ok=True,
            statuses=[f"正在把消息发送到 {project_name} 的项目会话…", "正在等待云端项目返回结果…"],
            reply=_project_processing_reply(session_project, polled_payload),
            meta={"action": "project_message_send", "project_id": project_id, "send": send_payload, "status": polled_payload, "project_name": project_name, "poll_project": True},
        )

    if generate_image_request:
        prompt = _clean_prompt_prefix(text)
        output_dir = _build_output_path("image")
        ok, payload, raw = _run_cli_json(["generate", "image", prompt, "--output-path", str(output_dir)], timeout=300)
        if not ok:
            return CliHandleResult(ok=False, statuses=["正在生成图片…"], reply=f"生成图片失败：{raw}", meta={"action": "generate_image"})
        return CliHandleResult(
            ok=True,
            statuses=["正在生成图片…"],
            reply=f"图片生成任务已完成。输出目录：{output_dir}\n{json.dumps(payload, ensure_ascii=False)[:3000]}",
            meta={"action": "generate_image", "result": payload, "output_path": str(output_dir)},
        )

    if generate_audio_request:
        prompt = _clean_prompt_prefix(text)
        output_file = _build_output_path("audio", ".mp3")
        ok, payload, raw = _run_cli_json(["generate", "audio", prompt, "--output-path", str(output_file)], timeout=300)
        if not ok:
            return CliHandleResult(ok=False, statuses=["正在生成语音…"], reply=f"生成语音失败：{raw}", meta={"action": "generate_audio"})
        return CliHandleResult(
            ok=True,
            statuses=["正在生成语音…"],
            reply=f"语音生成任务已完成。输出文件：{output_file}\n{json.dumps(payload, ensure_ascii=False)[:3000]}",
            meta={"action": "generate_audio", "result": payload, "output_path": str(output_file)},
        )

    if generate_video_request:
        prompt = _clean_prompt_prefix(text)
        output_dir = _build_output_path("video")
        ok, payload, raw = _run_cli_json(["generate", "video", "create", prompt, "--wait", "--output-path", str(output_dir)], timeout=900)
        if not ok:
            return CliHandleResult(ok=False, statuses=["正在生成视频…"], reply=f"生成视频失败：{raw}", meta={"action": "generate_video"})
        return CliHandleResult(
            ok=True,
            statuses=["正在生成视频…"],
            reply=f"视频生成任务已完成。输出目录：{output_dir}\n{json.dumps(payload, ensure_ascii=False)[:3000]}",
            meta={"action": "generate_video", "result": payload, "output_path": str(output_dir)},
        )

    if session_project:
        project_name = str(session_project.get("project_name") or "当前项目").strip()
        return CliHandleResult(
            ok=True,
            reply=f"当前工作台正在处理“{project_name}”。你现在可以直接继续提修改需求，或者回复“继续”“查看进度”来查询最新处理结果。",
            statuses=["正在理解你的项目会话意图…"],
            meta={"action": "project_help", "project_id": session_project.get("project_id")},
        )

    return CliHandleResult(
        ok=True,
        reply="云端工作台 CLI 已经接入。当前优先支持：登录授权、查看账号状态、创建网页应用、创建移动应用、续聊项目会话、生成图片、生成语音、生成视频、查看组织和空间。你可以直接说“帮我做一个介绍 OpenClaw 的网页”或“帮我登录云端账号”。",
        statuses=["正在理解你的工作台任务…"],
        meta={"action": "help"},
    )

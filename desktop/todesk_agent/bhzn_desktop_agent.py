import argparse
import asyncio
import base64
import ctypes
import hashlib
import json
import os
import platform
import queue
import random
import re
import socket
import string
import sys
import threading
import time
import urllib.request
import struct
from fractions import Fraction
from urllib.parse import urlparse
try:
    import tkinter as tk
    from tkinter import messagebox
except Exception:
    tk = None
    messagebox = None
from io import BytesIO
from pathlib import Path

import mss
import pyautogui
import pyperclip
import websocket
from PIL import Image
try:
    import Quartz
except Exception:
    Quartz = None
try:
    import numpy as np
    from aiortc import RTCConfiguration, RTCIceCandidate, RTCIceServer, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    from aiortc.sdp import candidate_from_sdp
    from av import VideoFrame
    AIORTC_AVAILABLE = True
except Exception:
    RTCConfiguration = None
    RTCIceCandidate = None
    RTCIceServer = None
    RTCPeerConnection = None
    RTCSessionDescription = None
    VideoStreamTrack = object
    VideoFrame = None
    candidate_from_sdp = None
    AIORTC_AVAILABLE = False


APP_NAME = "BHZN ToDesk Desktop Agent"
AGENT_VERSION = "0.1.21"
FRAME_INTERVAL = 0.10
DRAG_FRAME_INTERVAL = 0.05
HEARTBEAT_INTERVAL = 15.0
JPEG_QUALITY = 45
DRAG_JPEG_QUALITY = 34
MAX_SIDE = 1280
DRAG_MAX_SIDE = 1280
RECONNECT_MIN = 1.5
RECONNECT_MAX = 30.0
DRAG_FAST_MODE_SECONDS = 0.8
MAX_FILE_TRANSFER_BYTES = 100 * 1024 * 1024
MAX_IN_FLIGHT_FRAMES = 2
FRAME_BACKPRESSURE_GRACE = 1.25
FRAME_BACKPRESSURE_PAUSE = 0.75
try:
    RTC_TARGET_FPS = max(1, min(30, int(os.environ.get("BHZN_RTC_FPS", "18"))))
except Exception:
    RTC_TARGET_FPS = 18
RTC_FRAME_TIME_BASE = Fraction(1, 90000)
APPLICATION_SERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
BINARY_FRAME_MAGIC = b"BHZF1"
BINARY_FRAME_HEADER_LIMIT = 64 * 1024
RTC_QUALITY_PROFILES = {
    "data": {"profile": "data", "maxSide": 1280, "fps": 10, "jpegQuality": 42, "bitrateKbps": 1200},
    "balanced": {"profile": "balanced", "maxSide": 1920, "fps": 18, "jpegQuality": 58, "bitrateKbps": 3000},
    "hd": {"profile": "hd", "maxSide": 2560, "fps": 24, "jpegQuality": 66, "bitrateKbps": 6000},
    "lan": {"profile": "lan", "maxSide": 3840, "fps": 30, "jpegQuality": 74, "bitrateKbps": 12000},
    "relay": {"profile": "relay", "maxSide": 1280, "fps": 10, "jpegQuality": 45, "bitrateKbps": 1200},
}


def reveal_text(values, key: int) -> str:
    return "".join(chr(value ^ key) for value in values)


DEFAULT_SERVER = reveal_text(
    [50, 46, 46, 42, 41, 96, 117, 117, 46, 53, 62, 63, 41, 49, 116, 56, 50, 32, 52, 116, 46, 53, 42],
    0x5A,
)

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0
if platform.system().lower() == "windows":
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def config_dir() -> Path:
    system = platform.system().lower()
    if system == "windows":
        root = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(root) / "BHZN-ToDesk"
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / "BHZN-ToDesk"
    return Path.home() / ".config" / "bhzn-todesk"


def normalize_server_url(value: str) -> str:
    url = (value or DEFAULT_SERVER).strip().rstrip("/")
    if not url:
        url = DEFAULT_SERVER
    if not url.startswith(("http://", "https://", "ws://", "wss://")):
        url = "https://" + url
    if url == DEFAULT_SERVER.replace("https://", "http://"):
        url = DEFAULT_SERVER
    if url == DEFAULT_SERVER.replace("https://", "ws://"):
        url = DEFAULT_SERVER.replace("https://", "wss://")
    return url


def ws_url(server_url: str) -> str:
    url = normalize_server_url(server_url)
    if url.startswith("https://"):
        url = "wss://" + url[len("https://") :]
    elif url.startswith("http://"):
        url = "ws://" + url[len("http://") :]
    if url.startswith("ws://") and not is_local_url(url):
        raise ValueError("Cleartext websocket is disabled for non-local servers. Use https:// or wss://.")
    if not url.endswith("/ws"):
        url += "/ws"
    return url


def is_local_url(value: str) -> bool:
    try:
        host = (urlparse(value).hostname or "").lower()
    except Exception:
        return False
    return host in {"localhost", "127.0.0.1", "::1"} or host.startswith("127.")


def normalize_device_id(value: str) -> str:
    clean = "".join(ch for ch in value.upper() if ch in string.ascii_uppercase + string.digits)
    if len(clean) < 8:
        clean = (clean + random_token(8))[:8]
    return f"{clean[:4]}-{clean[4:8]}"


def random_token(length: int) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def random_code() -> str:
    return "".join(random.choice(string.digits) for _ in range(6))


def platform_name() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    return system or "desktop"


def accessibility_permission_available() -> bool:
    if platform_name() != "macos":
        return True
    try:
        app_services = ctypes.cdll.LoadLibrary(APPLICATION_SERVICES)
        app_services.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(app_services.AXIsProcessTrusted())
    except Exception:
        return False


def request_accessibility_permission() -> bool:
    if platform_name() != "macos":
        return True
    try:
        app_services = ctypes.cdll.LoadLibrary(APPLICATION_SERVICES)
        core_foundation = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        app_services.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
        app_services.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
        core_foundation.CFDictionaryCreate.restype = ctypes.c_void_p
        core_foundation.CFDictionaryCreate.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        core_foundation.CFRelease.argtypes = [ctypes.c_void_p]

        prompt_key = ctypes.c_void_p.in_dll(app_services, "kAXTrustedCheckOptionPrompt")
        true_value = ctypes.c_void_p.in_dll(core_foundation, "kCFBooleanTrue")
        keys = (ctypes.c_void_p * 1)(prompt_key)
        values = (ctypes.c_void_p * 1)(true_value)
        options = core_foundation.CFDictionaryCreate(None, keys, values, 1, None, None)
        if not options:
            return accessibility_permission_available()
        try:
            return bool(app_services.AXIsProcessTrustedWithOptions(options))
        finally:
            core_foundation.CFRelease(options)
    except Exception:
        return accessibility_permission_available()


class AgentConfig:
    def __init__(self, path: Path, data: dict):
        self.path = path
        self.server = normalize_server_url(data.get("server") or DEFAULT_SERVER)
        self.device_id = normalize_device_id(data.get("deviceId") or random_token(8))
        self.verification_code = str(data.get("verificationCode") or random_code())[:8]
        self.name = data.get("name") or default_device_name()

    @classmethod
    def load(cls, custom_path: str = ""):
        path = Path(custom_path).expanduser() if custom_path else config_dir() / "agent.json"
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                backup = path.with_suffix(f".{int(time.time())}.bad")
                try:
                    path.replace(backup)
                except Exception:
                    pass
        config = cls(path, data)
        config.save()
        return config

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "server": self.server,
            "deviceId": self.device_id,
            "verificationCode": self.verification_code,
            "name": self.name,
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def default_device_name() -> str:
    host = socket.gethostname() or "Desktop"
    if platform_name() == "windows":
        return f"BHZN Windows {host}"
    if platform_name() == "macos":
        return f"BHZN Mac {host}"
    return f"BHZN Desktop {host}"


def file_receive_dir() -> Path:
    return Path.home() / "Downloads" / "BHZN-ToDesk"


def safe_file_name(value: str) -> str:
    name = Path(str(value or "file.bin").replace("\\", "/")).name.strip()
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name).strip(". ")
    return name or "file.bin"


def unique_file_path(directory: Path, file_name: str) -> Path:
    clean = safe_file_name(file_name)
    path = directory / clean
    if not path.exists():
        return path
    stem = path.stem or "file"
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{int(time.time())}-{clean}"


def is_allowed_file_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    if parsed.scheme == "https":
        return True
    if parsed.scheme == "http" and (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}:
        return True
    return False


def normalize_quality_profile(value) -> dict:
    if isinstance(value, dict):
        name = str(value.get("profile") or value.get("name") or "balanced").lower()
    else:
        name = str(value or "balanced").lower()
    profile = dict(RTC_QUALITY_PROFILES.get(name) or RTC_QUALITY_PROFILES["balanced"])
    if isinstance(value, dict):
        try:
            profile["maxSide"] = max(640, min(3840, int(value.get("maxSide") or profile["maxSide"])))
        except Exception:
            pass
        try:
            profile["fps"] = max(1, min(30, int(value.get("fps") or profile["fps"])))
        except Exception:
            pass
        try:
            profile["jpegQuality"] = max(25, min(82, int(value.get("jpegQuality") or profile["jpegQuality"])))
        except Exception:
            pass
        try:
            profile["bitrateKbps"] = max(300, min(16000, int(value.get("bitrateKbps") or profile["bitrateKbps"])))
        except Exception:
            pass
    profile["profile"] = str(profile.get("profile") or name)
    return profile


class DesktopVideoTrack(VideoStreamTrack):
    def __init__(self, agent, quality=None):
        super().__init__()
        self.agent = agent
        self.quality = normalize_quality_profile(quality or {})
        self.frame_index = 0
        self.target_fps = max(1, min(30, int(self.quality.get("fps") or RTC_TARGET_FPS)))
        self.frame_interval = 1.0 / self.target_fps
        self.frame_duration = max(1, int(90000 / self.target_fps))
        self.next_frame_at = time.monotonic()

    async def recv(self):
        now = time.monotonic()
        if self.next_frame_at > now:
            await asyncio.sleep(self.next_frame_at - now)
        elif now - self.next_frame_at > self.frame_interval * 2:
            self.next_frame_at = now
        self.next_frame_at += self.frame_interval

        image = self.agent.capture_image_for_rtc(self.quality)
        array = np.asarray(image.convert("RGB"))
        frame = VideoFrame.from_ndarray(array, format="rgb24")
        frame.pts = self.frame_index * self.frame_duration
        frame.time_base = RTC_FRAME_TIME_BASE
        self.frame_index += 1
        return frame


class DesktopAgent:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.ws = None
        self.running = True
        self.connected = False
        self.capture_enabled = False
        self.bridge_screen_trusted = os.environ.get("BHZN_MAC_SCREEN_TRUSTED") == "1"
        self.bridge_input_trusted = os.environ.get("BHZN_MAC_INPUT_TRUSTED") == "1"
        self.control_enabled = self.input_permission_available()
        self.screen = {"width": 0, "height": 0, "inputWidth": 0, "inputHeight": 0}
        self.input_origin = {"x": 0, "y": 0}
        self.input_queue: queue.Queue[dict] = queue.Queue(maxsize=512)
        self.capture_thread = None
        self.capture_requested = False
        self.relay_quality = normalize_quality_profile("relay")
        self.control_sessions: set[str] = set()
        self.frame_id = 0
        self.frame_lock = threading.Lock()
        self.frames_in_flight: dict[int, float] = {}
        self.last_frame_ack_at = 0.0
        self.bridge_input_thread = None
        self.heartbeat_thread = None
        self.rtc_loop = None
        self.rtc_thread = None
        self.rtc_sessions = {}
        self.rtc_lock = threading.Lock()
        self.rtc_capture_sessions: set[str] = set()
        self.dragging = False
        self.drag_button = "left"
        self.fast_frame_until = 0.0
        self.pointer_pos = None
        self.input_thread = threading.Thread(target=self.input_loop, daemon=True)
        self.input_thread.start()
        self.start_rtc_loop()
        if self.macos_capture_bridge_enabled():
            self.bridge_input_thread = threading.Thread(target=self.bridge_input_loop, daemon=True)
            self.bridge_input_thread.start()

    def start_rtc_loop(self):
        if not AIORTC_AVAILABLE:
            return
        self.rtc_loop = asyncio.new_event_loop()
        self.rtc_thread = threading.Thread(target=self.run_rtc_loop, daemon=True)
        self.rtc_thread.start()

    def run_rtc_loop(self):
        asyncio.set_event_loop(self.rtc_loop)
        self.rtc_loop.run_forever()

    def run_rtc_coro(self, coro):
        if not self.rtc_loop:
            return None
        return asyncio.run_coroutine_threadsafe(coro, self.rtc_loop)

    def run(self):
        delay = RECONNECT_MIN
        while self.running:
            try:
                self.connect_once()
                delay = RECONNECT_MIN
            except KeyboardInterrupt:
                self.running = False
                break
            except Exception as exc:
                self.connected = False
                print(f"[agent] connection ended: {exc}", flush=True)
            if not self.running:
                break
            time.sleep(delay)
            delay = min(RECONNECT_MAX, delay * 2)

    def stop(self):
        self.running = False
        self.close()

    def connect_once(self):
        url = ws_url(self.config.server)
        print(f"[agent] connecting {url}", flush=True)
        self.ws = websocket.WebSocketApp(
            url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        self.ws.run_forever(ping_interval=20, ping_timeout=10)

    def on_open(self, _ws):
        self.connected = True
        self.capture_requested = False
        self.capture_enabled = self.screen_permission_available()
        self.control_enabled = self.input_permission_available()
        print("[agent] connected", flush=True)
        print(
            "[agent] screen capture permission: "
            + ("available" if self.capture_enabled else "not granted"),
            flush=True,
        )
        self.send_status("hello-device")
        self.heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()

    def on_close(self, _ws, status_code, message):
        self.connected = False
        self.close_all_rtc_sessions()
        print(f"[agent] closed {status_code or ''} {message or ''}", flush=True)

    def on_error(self, _ws, error):
        self.connected = False
        print(f"[agent] websocket error: {error}", flush=True)

    def on_message(self, _ws, raw: str):
        try:
            msg = json.loads(raw)
        except Exception:
            return
        msg_type = msg.get("type")
        if msg_type == "hello":
            print(f"[agent] device online id={self.config.device_id}", flush=True)
            return
        if msg_type == "control-request":
            self.add_control_session(msg)
            self.send_status("status")
            return
        if msg_type == "stop-control":
            self.remove_control_session(msg)
            self.send_status("status")
            return
        if msg_type == "rtc-request":
            self.handle_rtc_request(msg)
            return
        if msg_type in {"rtc-offer", "rtc-ice-candidate", "rtc-stopped"}:
            self.handle_rtc_signal(msg)
            return
        if msg_type == "frame-ack":
            self.handle_frame_ack(msg)
            return
        if msg_type == "server-replaced":
            print("[agent] another client replaced this device session", flush=True)
            self.close()
            return
        if msg_type == "file-transfer":
            self.start_file_transfer(msg)
            return
        if msg_type == "input":
            try:
                self.input_queue.put_nowait(msg)
            except queue.Full:
                pass

    def close(self):
        self.connected = False
        self.capture_requested = False
        self.control_sessions.clear()
        self.close_all_rtc_sessions()
        self.reset_frame_backpressure()
        self.stop_macos_capture_bridge()
        self.release_drag()
        if self.ws:
            self.ws.close()

    def send_json(self, payload: dict) -> bool:
        ws = self.ws
        if not ws or not self.connected:
            return False
        try:
            ws.send(json.dumps(payload, separators=(",", ":")))
            return True
        except Exception as exc:
            self.connected = False
            print(f"[agent] send failed: {exc}", flush=True)
            try:
                ws.close()
            except Exception:
                pass
            return False

    def send_binary(self, payload: bytes) -> bool:
        ws = self.ws
        if not ws or not self.connected:
            return False
        try:
            ws.send(payload, opcode=websocket.ABNF.OPCODE_BINARY)
            return True
        except Exception as exc:
            self.connected = False
            print(f"[agent] binary send failed: {exc}", flush=True)
            try:
                ws.close()
            except Exception:
                pass
            return False

    def base_status(self, msg_type: str) -> dict:
        return {
            "type": msg_type,
            "id": self.config.device_id,
            "verificationCode": self.config.verification_code,
            "name": self.config.name,
            "model": platform.machine() or platform.processor() or "",
            "platform": platform_name(),
            "osVersion": platform.platform(),
            "agentVersion": AGENT_VERSION,
            "permissions": {
                "screenCapture": self.capture_enabled,
                "inputControl": self.control_enabled,
            },
            "controlEnabled": bool(self.control_enabled and self.capture_enabled),
            "screen": self.screen,
            "rtcCapabilities": self.rtc_capabilities(),
        }

    def send_status(self, msg_type: str = "status"):
        self.capture_enabled = self.screen_permission_available()
        self.control_enabled = self.input_permission_available()
        self.send_json(self.base_status(msg_type))

    def rtc_capabilities(self) -> dict:
        available = bool(AIORTC_AVAILABLE)
        return {
            "webrtc": available,
            "video": available,
            "dataChannel": available,
            "localNetwork": True,
            "codecs": ["H264", "VP8"] if available else [],
            "maxFps": RTC_TARGET_FPS if available else 0,
            "version": "aiortc-1",
        }

    def handle_rtc_request(self, msg: dict):
        session_id = str(msg.get("sessionId") or "")
        if not AIORTC_AVAILABLE or not self.rtc_loop:
            self.send_rtc_state(session_id, "failed", error="aiortc_unavailable")
            return
        if not self.screen_permission_available():
            self.send_rtc_state(session_id, "failed", error="screen_not_ready")
            self.send_status("status")
            return
        print(f"[agent] rtc requested session={session_id}", flush=True)
        future = self.run_rtc_coro(self.start_rtc_session(msg))
        if future:
            future.add_done_callback(lambda item: self.log_rtc_future("start", session_id, item))

    def handle_rtc_signal(self, msg: dict):
        session_id = str(msg.get("sessionId") or "")
        if msg.get("type") == "rtc-stopped":
            self.close_rtc_session(session_id)
            return
        if not AIORTC_AVAILABLE or not self.rtc_loop:
            self.send_rtc_state(session_id, "failed", error="aiortc_unavailable")
            return
        future = self.run_rtc_coro(self.handle_rtc_signal_async(msg))
        if future:
            future.add_done_callback(lambda item: self.log_rtc_future("signal", session_id, item))

    def log_rtc_future(self, action: str, session_id: str, future):
        try:
            future.result()
        except Exception as exc:
            print(f"[agent] rtc {action} failed session={session_id}: {exc}", flush=True)
            self.mark_rtc_capture_inactive(session_id)
            self.send_rtc_state(session_id, "failed", error=str(exc))

    def send_rtc_state(self, session_id: str, state: str, candidate_type: str = "unknown", error: str = "", stats=None):
        payload = {
            "type": "rtc-state",
            "sessionId": session_id,
            "deviceId": self.config.device_id,
            "state": state,
            "selectedCandidateType": candidate_type,
            "error": str(error or "")[:400],
        }
        if stats:
            payload.update({
                "rttMs": int(max(0, stats.get("rttMs", 0))),
                "bitrateKbps": int(max(0, stats.get("bitrateKbps", 0))),
                "bytesSent": int(max(0, stats.get("bytesSent", 0))),
                "bytesReceived": int(max(0, stats.get("bytesReceived", 0))),
                "packetsLost": int(max(0, stats.get("packetsLost", 0))),
            })
        self.send_json(payload)

    async def start_rtc_session(self, msg: dict):
        session_id = str(msg.get("sessionId") or "")
        if not session_id:
            return
        await self.close_rtc_session_async(session_id)
        self.suppress_relay_capture_for_rtc(session_id)
        quality = normalize_quality_profile(msg.get("quality") or msg.get("qualityProfile") or {})
        ice_servers = []
        for item in msg.get("iceServers") or []:
            urls = item.get("urls") or []
            if isinstance(urls, str):
                urls = [urls]
            if not urls:
                continue
            ice_servers.append(
                RTCIceServer(
                    urls=urls,
                    username=item.get("username"),
                    credential=item.get("credential"),
                )
            )
        pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
        track = DesktopVideoTrack(self, quality)
        pc.addTrack(track)

        @pc.on("icecandidate")
        async def on_icecandidate(candidate):
            if candidate is None:
                return
            self.send_json(
                {
                    "type": "rtc-ice-candidate",
                    "sessionId": session_id,
                    "deviceId": self.config.device_id,
                    "candidate": {
                        "candidate": candidate.to_sdp(),
                        "sdpMid": candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex,
                    },
                }
            )

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            state = pc.connectionState
            stats = await self.rtc_connection_stats(pc)
            self.send_rtc_state(session_id, state, candidate_type=str(stats.get("candidateType") or "unknown"), stats=stats)
            if state == "connected":
                with self.rtc_lock:
                    session = self.rtc_sessions.get(session_id)
                    if session and not session.get("statsTask"):
                        session["statsTask"] = asyncio.create_task(self.rtc_stats_loop(session_id, pc))
            if state in {"failed", "closed"}:
                await self.close_rtc_session_async(session_id)

        @pc.on("datachannel")
        def on_datachannel(channel):
            if channel.label == "control":
                @channel.on("message")
                def on_control_message(message):
                    self.handle_rtc_control_message(session_id, channel, message)

        with self.rtc_lock:
            self.rtc_sessions[session_id] = {"pc": pc, "track": track, "quality": quality, "statsTask": None}
            self.rtc_capture_sessions.add(session_id)
        print(f"[agent] rtc quality session={session_id} profile={quality.get('profile')} fps={quality.get('fps')} maxSide={quality.get('maxSide')}", flush=True)
        self.send_rtc_state(session_id, "checking")

    async def handle_rtc_signal_async(self, msg: dict):
        session_id = str(msg.get("sessionId") or "")
        with self.rtc_lock:
            session = self.rtc_sessions.get(session_id)
        if not session:
            self.send_rtc_state(session_id, "failed", error="bad_rtc_session")
            return
        pc = session["pc"]
        msg_type = msg.get("type")
        if msg_type == "rtc-offer":
            sdp = str(msg.get("sdp") or "")
            if not sdp:
                self.send_rtc_state(session_id, "failed", error="empty_offer")
                return
            await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            self.send_json(
                {
                    "type": "rtc-answer",
                    "sessionId": session_id,
                    "deviceId": self.config.device_id,
                    "sdp": pc.localDescription.sdp,
                }
            )
            return
        if msg_type == "rtc-ice-candidate":
            candidate = self.parse_remote_ice_candidate(msg.get("candidate") or {})
            if candidate is not None:
                await pc.addIceCandidate(candidate)
            return

    def parse_remote_ice_candidate(self, payload: dict):
        raw = str(payload.get("candidate") or "")
        if not raw or candidate_from_sdp is None:
            return None
        if raw.startswith("candidate:"):
            raw = raw[len("candidate:") :]
        candidate = candidate_from_sdp(raw)
        candidate.sdpMid = payload.get("sdpMid")
        candidate.sdpMLineIndex = payload.get("sdpMLineIndex")
        return candidate

    def handle_rtc_control_message(self, session_id: str, channel, message):
        try:
            payload = json.loads(message if isinstance(message, str) else message.decode("utf-8"))
        except Exception:
            return
        if payload.get("type") != "input":
            return
        payload["sessionId"] = session_id
        try:
            self.handle_input(payload)
            self.control_enabled = True
            result = {
                "type": "input-result",
                "sessionId": session_id,
                "inputId": str(payload.get("inputId") or ""),
                "action": str(payload.get("action") or ""),
                "ok": True,
                "error": "",
            }
        except Exception as exc:
            self.control_enabled = False
            result = {
                "type": "input-result",
                "sessionId": session_id,
                "inputId": str(payload.get("inputId") or ""),
                "action": str(payload.get("action") or ""),
                "ok": False,
                "error": str(exc)[:400],
            }
        try:
            channel.send(json.dumps(result, separators=(",", ":")))
        except Exception:
            pass

    def close_rtc_session(self, session_id: str):
        if not session_id or not self.rtc_loop:
            return
        future = self.run_rtc_coro(self.close_rtc_session_async(session_id))
        if future:
            future.add_done_callback(lambda item: self.log_rtc_future("close", session_id, item))

    def close_all_rtc_sessions(self):
        if not self.rtc_loop:
            return
        with self.rtc_lock:
            session_ids = list(self.rtc_sessions.keys())
        for session_id in session_ids:
            self.close_rtc_session(session_id)

    async def close_rtc_session_async(self, session_id: str):
        with self.rtc_lock:
            session = self.rtc_sessions.pop(session_id, None)
            self.rtc_capture_sessions.discard(session_id)
        if not session:
            return
        task = session.get("statsTask")
        if task:
            task.cancel()
        pc = session.get("pc")
        if pc:
            await pc.close()

    def rtc_capture_active(self) -> bool:
        with self.rtc_lock:
            return bool(self.rtc_capture_sessions)

    def mark_rtc_capture_inactive(self, session_id: str):
        with self.rtc_lock:
            self.rtc_capture_sessions.discard(session_id)

    def suppress_relay_capture_for_rtc(self, session_id: str):
        with self.rtc_lock:
            self.rtc_capture_sessions.add(session_id)
        if self.capture_requested or self.control_sessions:
            print("[agent] stopping websocket relay capture for rtc", flush=True)
        self.control_sessions.clear()
        self.capture_requested = False
        self.reset_frame_backpressure()
        self.stop_macos_capture_bridge()

    async def rtc_connection_stats(self, pc) -> dict:
        result = {
            "candidateType": "unknown",
            "rttMs": 0,
            "bitrateKbps": 0,
            "bytesSent": 0,
            "bytesReceived": 0,
            "packetsLost": 0,
        }
        try:
            stats = await pc.getStats()
            for report in stats.values():
                report_type = getattr(report, "type", "")
                if report_type == "candidate-pair" and (getattr(report, "selected", False) or getattr(report, "state", "") == "succeeded"):
                    local_id = getattr(report, "localCandidateId", "")
                    candidate = stats.get(local_id)
                    candidate_type = getattr(candidate, "candidateType", "") if candidate else ""
                    result["candidateType"] = candidate_type or result["candidateType"]
                    result["rttMs"] = int(max(0, float(getattr(report, "currentRoundTripTime", 0) or 0) * 1000))
                    result["bytesSent"] = max(result["bytesSent"], int(getattr(report, "bytesSent", 0) or 0))
                    result["bytesReceived"] = max(result["bytesReceived"], int(getattr(report, "bytesReceived", 0) or 0))
                elif report_type == "outbound-rtp" and getattr(report, "kind", "") == "video":
                    result["bytesSent"] += int(getattr(report, "bytesSent", 0) or 0)
                    result["packetsLost"] += int(getattr(report, "packetsLost", 0) or 0)
        except Exception:
            pass
        return result

    async def rtc_stats_loop(self, session_id: str, pc):
        previous_bytes = 0
        previous_at = time.monotonic()
        while True:
            await asyncio.sleep(2.0)
            with self.rtc_lock:
                if session_id not in self.rtc_sessions:
                    return
            stats = await self.rtc_connection_stats(pc)
            now_value = time.monotonic()
            bytes_sent = int(stats.get("bytesSent") or 0)
            elapsed = max(0.001, now_value - previous_at)
            if previous_bytes:
                stats["bitrateKbps"] = int(max(0, (bytes_sent - previous_bytes) * 8 / elapsed / 1000))
            previous_bytes = bytes_sent
            previous_at = now_value
            self.send_rtc_state(
                session_id,
                getattr(pc, "connectionState", "connected") or "connected",
                candidate_type=str(stats.get("candidateType") or "unknown"),
                stats=stats,
            )

    def macos_capture_bridge_enabled(self) -> bool:
        return platform_name() == "macos" and os.environ.get("BHZN_MAC_CAPTURE_BRIDGE") == "1"

    def input_permission_available(self) -> bool:
        if platform_name() != "macos":
            return accessibility_permission_available()
        if os.environ.get("BHZN_MAC_INPUT_BRIDGE") == "1":
            return self.bridge_input_trusted
        return accessibility_permission_available()

    def send_file_transfer_status(self, transfer_id: str, status: str, path: str = "", bytes_count: int = 0, error: str = ""):
        self.send_json(
            {
                "type": "file-transfer-status",
                "transferId": transfer_id,
                "status": status,
                "path": path,
                "bytes": max(0, int(bytes_count or 0)),
                "error": str(error or "")[:400],
            }
        )

    def start_file_transfer(self, msg: dict):
        transfer_id = str(msg.get("transferId") or "")
        if not transfer_id:
            return
        self.send_file_transfer_status(transfer_id, "downloading")
        thread = threading.Thread(target=self.receive_file_transfer, args=(msg,), daemon=True)
        thread.start()

    def receive_file_transfer(self, msg: dict):
        transfer_id = str(msg.get("transferId") or "")
        file_name = safe_file_name(str(msg.get("fileName") or "file.bin"))
        path = ""
        try:
            expected_size = int(msg.get("size") or 0)
            expected_sha256 = str(msg.get("sha256") or "").lower()
            url = str(msg.get("url") or "")
            if expected_size <= 0:
                raise ValueError("empty file")
            if expected_size > MAX_FILE_TRANSFER_BYTES:
                raise ValueError("file too large")
            if len(expected_sha256) != 64 or any(ch not in string.hexdigits for ch in expected_sha256):
                raise ValueError("bad sha256")
            if not is_allowed_file_url(url):
                raise ValueError("file transfer requires https url")

            target_dir = file_receive_dir()
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = unique_file_path(target_dir, file_name)
            temp_path = target_path.with_name(target_path.name + ".download")
            hasher = hashlib.sha256()
            downloaded = 0
            request = urllib.request.Request(url, headers={"User-Agent": f"BHZN-ToDesk-Agent/{AGENT_VERSION}"})
            with urllib.request.urlopen(request, timeout=60) as response, temp_path.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > expected_size:
                        raise ValueError("file larger than expected")
                    hasher.update(chunk)
                    output.write(chunk)
            if downloaded != expected_size:
                raise ValueError(f"file size mismatch expected={expected_size} actual={downloaded}")
            if hasher.hexdigest().lower() != expected_sha256:
                raise ValueError("file sha256 mismatch")
            temp_path.replace(target_path)
            path = str(target_path)
            print(f"[agent] file saved {path}", flush=True)
            print(
                "__BHZN_FILE_RECEIVED__"
                + json.dumps(
                    {"path": path, "fileName": target_path.name, "bytes": downloaded},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                flush=True,
            )
            self.send_file_transfer_status(transfer_id, "saved", path=path, bytes_count=downloaded)
        except Exception as exc:
            try:
                if "temp_path" in locals() and temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass
            print(f"[agent] file transfer failed: {exc}", flush=True)
            self.send_file_transfer_status(transfer_id, "failed", path=path, error=str(exc))

    def heartbeat_loop(self):
        while self.running and self.connected:
            time.sleep(HEARTBEAT_INTERVAL)
            self.capture_enabled = self.screen_permission_available()
            self.send_status("heartbeat")

    def screen_permission_available(self) -> bool:
        if platform_name() != "macos":
            return True
        if self.macos_capture_bridge_enabled():
            return self.bridge_screen_trusted
        if Quartz is None or not hasattr(Quartz, "CGPreflightScreenCaptureAccess"):
            return False
        try:
            return bool(Quartz.CGPreflightScreenCaptureAccess())
        except Exception:
            return False

    def add_control_session(self, msg: dict):
        if self.rtc_capture_active():
            self.capture_requested = False
            self.stop_macos_capture_bridge()
            return
        self.relay_quality = normalize_quality_profile(msg.get("quality") or "relay")
        session_id = str(msg.get("sessionId") or "").strip()
        if session_id:
            self.control_sessions.add(session_id)
        controller_count = self.controller_count_from_message(msg)
        if controller_count <= 0 and session_id:
            controller_count = len(self.control_sessions)
        if controller_count > len(self.control_sessions) and not session_id:
            self.control_sessions.add("__unknown__")
        self.start_capture()

    def remove_control_session(self, msg: dict):
        session_id = str(msg.get("sessionId") or "").strip()
        if session_id:
            self.control_sessions.discard(session_id)
        controller_count = self.controller_count_from_message(msg)
        if controller_count <= 0:
            self.control_sessions.clear()
        elif len(self.control_sessions) > controller_count:
            self.control_sessions = set(list(self.control_sessions)[:controller_count])
        if controller_count > 0 and not self.control_sessions:
            self.control_sessions.add("__unknown__")
        if self.control_sessions or controller_count > 0:
            if self.rtc_capture_active():
                self.capture_requested = False
                self.stop_macos_capture_bridge()
                return
            self.capture_requested = True
            return
        self.capture_requested = False
        self.reset_frame_backpressure()
        self.stop_macos_capture_bridge()

    @staticmethod
    def controller_count_from_message(msg: dict) -> int:
        try:
            return max(0, int(msg.get("controllerCount") or 0))
        except Exception:
            return 0

    def next_frame_id(self) -> int:
        with self.frame_lock:
            self.frame_id += 1
            return self.frame_id

    def reset_frame_backpressure(self):
        with self.frame_lock:
            self.frames_in_flight.clear()
            self.last_frame_ack_at = 0.0

    def note_frame_sent(self, frame_id: int):
        with self.frame_lock:
            now = time.time()
            self.frames_in_flight[frame_id] = now
            stale_before = now - FRAME_BACKPRESSURE_GRACE
            for old_frame_id, sent_at in list(self.frames_in_flight.items()):
                if sent_at < stale_before:
                    self.frames_in_flight.pop(old_frame_id, None)

    def handle_frame_ack(self, msg: dict):
        try:
            frame_id = int(msg.get("frameId") or 0)
        except Exception:
            frame_id = 0
        with self.frame_lock:
            self.last_frame_ack_at = time.time()
            if frame_id:
                for old_frame_id in list(self.frames_in_flight):
                    if old_frame_id <= frame_id:
                        self.frames_in_flight.pop(old_frame_id, None)

    def should_pause_for_frame_backpressure(self) -> bool:
        if self.rtc_capture_active():
            return True
        if not self.control_sessions:
            return True
        with self.frame_lock:
            now = time.time()
            stale_before = now - FRAME_BACKPRESSURE_GRACE
            for frame_id, sent_at in list(self.frames_in_flight.items()):
                if sent_at < stale_before:
                    self.frames_in_flight.pop(frame_id, None)
            return len(self.frames_in_flight) >= MAX_IN_FLIGHT_FRAMES

    def start_capture(self):
        if self.rtc_capture_active():
            self.capture_requested = False
            self.stop_macos_capture_bridge()
            return
        self.capture_requested = True
        if not self.screen_permission_available():
            self.capture_enabled = False
            self.send_status("status")
            print("[agent] screen capture permission is not granted", flush=True)
            return
        self.capture_enabled = True
        if self.capture_thread and self.capture_thread.is_alive():
            return
        if self.macos_capture_bridge_enabled():
            self.capture_thread = threading.Thread(target=self.capture_bridge_loop, daemon=True)
            self.capture_thread.start()
            return
        self.capture_thread = threading.Thread(target=self.capture_loop, daemon=True)
        self.capture_thread.start()

    def capture_bridge_loop(self):
        bridge_started = False
        try:
            while self.running and self.connected and self.capture_requested and not self.rtc_capture_active():
                if self.should_pause_for_frame_backpressure():
                    if bridge_started:
                        self.stop_macos_capture_bridge()
                        bridge_started = False
                    time.sleep(FRAME_BACKPRESSURE_PAUSE)
                    continue
                if not bridge_started:
                    self.start_macos_capture_bridge()
                    bridge_started = True
                time.sleep(0.2)
        finally:
            if bridge_started:
                self.stop_macos_capture_bridge()

    def start_macos_capture_bridge(self):
        print("__BHZN_CAPTURE__" + json.dumps({"action": "start"}, separators=(",", ":")), flush=True)

    def stop_macos_capture_bridge(self):
        if self.macos_capture_bridge_enabled():
            print("__BHZN_CAPTURE__" + json.dumps({"action": "stop"}, separators=(",", ":")), flush=True)

    def bridge_input_loop(self):
        while self.running:
            try:
                line = sys.stdin.buffer.readline()
            except Exception:
                break
            if not line:
                break
            line = line.rstrip(b"\r\n")
            if line.startswith(b"__BHZN_FRAME_BYTES__"):
                try:
                    payload = json.loads(line[len(b"__BHZN_FRAME_BYTES__") :].decode("utf-8"))
                    byte_count = max(0, min(20_000_000, int(payload.get("bytes") or 0)))
                    if byte_count <= 0:
                        continue
                    image = sys.stdin.buffer.read(byte_count)
                    if len(image) != byte_count:
                        break
                    if self.should_pause_for_frame_backpressure():
                        continue
                    self.send_bridge_binary_frame(payload, image)
                except Exception as exc:
                    self.capture_enabled = False
                    print(f"[agent] bridge binary frame failed: {exc}", flush=True)
                continue
            if not line.startswith(b"__BHZN_FRAME__"):
                if line.startswith(b"__BHZN_PERMISSION__"):
                    self.handle_bridge_permission(line[len(b"__BHZN_PERMISSION__") :].decode("utf-8", errors="replace"))
                continue
            try:
                payload = json.loads(line[len(b"__BHZN_FRAME__") :].decode("utf-8"))
                if self.should_pause_for_frame_backpressure():
                    continue
                self.send_bridge_frame(payload)
            except Exception as exc:
                self.capture_enabled = False
                print(f"[agent] bridge frame failed: {exc}", flush=True)

    def handle_bridge_permission(self, json_text: str):
        try:
            payload = json.loads(json_text)
        except Exception:
            return
        old_screen = self.bridge_screen_trusted
        old_input = self.bridge_input_trusted
        self.bridge_screen_trusted = bool(payload.get("screen"))
        self.bridge_input_trusted = bool(payload.get("input"))
        if old_screen == self.bridge_screen_trusted and old_input == self.bridge_input_trusted:
            return
        self.capture_enabled = self.screen_permission_available()
        self.control_enabled = self.input_permission_available()
        self.send_status("status")
        if self.capture_requested and self.capture_enabled and not (self.capture_thread and self.capture_thread.is_alive()):
            self.start_capture()

    def send_bridge_frame(self, payload: dict):
        if self.rtc_capture_active() or not self.control_sessions:
            return
        image = str(payload.get("image") or "")
        width = int(payload.get("width") or 0)
        height = int(payload.get("height") or 0)
        input_width = int(payload.get("inputWidth") or width)
        input_height = int(payload.get("inputHeight") or height)
        if not image or width <= 0 or height <= 0:
            return
        self.screen = {
            "width": width,
            "height": height,
            "inputWidth": input_width,
            "inputHeight": input_height,
        }
        self.input_origin = {"x": 0, "y": 0}
        self.capture_enabled = True
        frame_id = self.next_frame_id()
        if self.send_json(
            {
                "type": "frame",
                "frameId": frame_id,
                "image": image,
                "width": width,
                "height": height,
                "timestamp": int(payload.get("timestamp") or time.time() * 1000),
            }
        ):
            self.note_frame_sent(frame_id)

    def send_bridge_binary_frame(self, payload: dict, image: bytes):
        if self.rtc_capture_active() or not self.control_sessions:
            return
        width = int(payload.get("width") or 0)
        height = int(payload.get("height") or 0)
        input_width = int(payload.get("inputWidth") or width)
        input_height = int(payload.get("inputHeight") or height)
        if not image or width <= 0 or height <= 0:
            return
        self.screen = {
            "width": width,
            "height": height,
            "inputWidth": input_width,
            "inputHeight": input_height,
        }
        self.input_origin = {"x": 0, "y": 0}
        self.capture_enabled = True
        frame_id = self.next_frame_id()
        header = {
            "frameId": frame_id,
            "frameKind": "jpeg",
            "width": width,
            "height": height,
            "inputWidth": input_width,
            "inputHeight": input_height,
            "timestamp": int(payload.get("timestamp") or time.time() * 1000),
        }
        encoded_header = json.dumps(header, separators=(",", ":")).encode("utf-8")
        if len(encoded_header) > BINARY_FRAME_HEADER_LIMIT:
            return
        packet = BINARY_FRAME_MAGIC + struct.pack("<I", len(encoded_header)) + encoded_header + image
        if self.send_binary(packet):
            self.note_frame_sent(frame_id)

    def capture_loop(self):
        last_error_at = 0.0
        use_quartz_capture = self.quartz_capture_available()
        sct = None
        try:
            if not use_quartz_capture:
                sct = mss.mss()
            while self.running and self.connected and self.capture_requested and not self.rtc_capture_active():
                if self.should_pause_for_frame_backpressure():
                    time.sleep(FRAME_BACKPRESSURE_PAUSE)
                    continue
                start = time.time()
                fast_frame = start < self.fast_frame_until
                try:
                    if use_quartz_capture:
                        image, monitor = self.capture_quartz_display()
                    else:
                        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                        shot = sct.grab(monitor)
                        image = Image.frombytes("RGB", shot.size, shot.rgb)
                    self.send_frame(image, monitor, fast_frame)
                except Exception as exc:
                    if not use_quartz_capture and self.quartz_capture_available():
                        use_quartz_capture = True
                        now = time.time()
                        if now - last_error_at > 5:
                            print(f"[agent] mss capture failed, switching to Quartz capture: {exc}", flush=True)
                            last_error_at = now
                        continue
                    self.capture_enabled = False
                    now = time.time()
                    if now - last_error_at > 5:
                        print(f"[agent] capture failed: {exc}", flush=True)
                        self.send_status("status")
                        last_error_at = now
                elapsed = time.time() - start
                target_interval = DRAG_FRAME_INTERVAL if fast_frame else FRAME_INTERVAL
                time.sleep(max(0.001, target_interval - elapsed))
        finally:
            if sct is not None:
                try:
                    sct.close()
                except Exception:
                    pass

    def capture_image_for_rtc(self, quality=None) -> Image.Image:
        quality_profile = normalize_quality_profile(quality or {})
        if platform_name() == "macos" and self.quartz_capture_available():
            image, monitor = self.capture_quartz_display()
        else:
            with mss.mss() as sct:
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                shot = sct.grab(monitor)
                image = Image.frombytes("RGB", shot.size, shot.rgb)
        self.screen = {
            "width": 0,
            "height": 0,
            "inputWidth": int(monitor["width"]),
            "inputHeight": int(monitor["height"]),
        }
        self.input_origin = {
            "x": int(monitor.get("left", 0)),
            "y": int(monitor.get("top", 0)),
        }
        image = self.resize_for_wire(image, int(quality_profile.get("maxSide") or MAX_SIDE))
        self.screen["width"], self.screen["height"] = image.size
        self.capture_enabled = True
        return image

    def send_frame(self, image: Image.Image, monitor: dict, fast_frame: bool):
        quality = getattr(self, "relay_quality", normalize_quality_profile("relay"))
        self.screen = {
            "width": 0,
            "height": 0,
            "inputWidth": int(monitor["width"]),
            "inputHeight": int(monitor["height"]),
        }
        self.input_origin = {
            "x": int(monitor.get("left", 0)),
            "y": int(monitor.get("top", 0)),
        }
        image = self.resize_for_wire(image, DRAG_MAX_SIDE if fast_frame else int(quality.get("maxSide") or MAX_SIDE))
        self.screen["width"], self.screen["height"] = image.size
        encoded = self.encode_jpeg(image, DRAG_JPEG_QUALITY if fast_frame else int(quality.get("jpegQuality") or JPEG_QUALITY))
        self.capture_enabled = True
        frame_id = self.next_frame_id()
        if self.send_json(
            {
                "type": "frame",
                "frameId": frame_id,
                "image": encoded,
                "width": image.size[0],
                "height": image.size[1],
                "timestamp": int(time.time() * 1000),
            }
        ):
            self.note_frame_sent(frame_id)

    @staticmethod
    def quartz_capture_available() -> bool:
        return (
            platform_name() == "macos"
            and Quartz is not None
            and hasattr(Quartz, "CGMainDisplayID")
            and hasattr(Quartz, "CGDisplayCreateImage")
            and hasattr(Quartz, "CGImageGetDataProvider")
            and hasattr(Quartz, "CGDataProviderCopyData")
        )

    def capture_quartz_display(self) -> tuple[Image.Image, dict]:
        display_id = Quartz.CGMainDisplayID()
        cg_image = Quartz.CGDisplayCreateImage(display_id)
        if cg_image is None:
            raise RuntimeError("Quartz CGDisplayCreateImage failed")
        width = int(Quartz.CGImageGetWidth(cg_image))
        height = int(Quartz.CGImageGetHeight(cg_image))
        bytes_per_row = int(Quartz.CGImageGetBytesPerRow(cg_image))
        provider = Quartz.CGImageGetDataProvider(cg_image)
        data = Quartz.CGDataProviderCopyData(provider)
        image = Image.frombuffer("RGB", (width, height), bytes(data), "raw", "BGRX", bytes_per_row, 1)
        monitor = {"left": 0, "top": 0, "width": width, "height": height}
        return image, monitor

    def resize_for_wire(self, image: Image.Image, max_side_limit: int = MAX_SIDE) -> Image.Image:
        width, height = image.size
        max_side = max(width, height)
        if max_side <= max_side_limit:
            return image
        scale = max_side_limit / float(max_side)
        new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        return image.resize(new_size, Image.Resampling.BILINEAR)

    def encode_jpeg(self, image: Image.Image, quality: int = JPEG_QUALITY) -> str:
        out = BytesIO()
        image.save(out, format="JPEG", quality=quality, optimize=False)
        return base64.b64encode(out.getvalue()).decode("ascii")

    def input_loop(self):
        while self.running:
            msg = self.input_queue.get()
            try:
                self.handle_input(msg)
                self.control_enabled = True
                self.send_input_result(msg, True)
            except Exception as exc:
                self.control_enabled = False
                print(f"[agent] input failed: {exc}", flush=True)
                self.send_input_result(msg, False, str(exc))
                self.send_status("status")

    def send_input_result(self, msg: dict, ok: bool, error: str = ""):
        input_id = str(msg.get("inputId") or "")
        session_id = str(msg.get("sessionId") or "")
        if not input_id and not session_id:
            return
        self.send_json(
            {
                "type": "input-result",
                "sessionId": session_id,
                "inputId": input_id,
                "action": str(msg.get("action") or ""),
                "ok": bool(ok),
                "error": str(error or "")[:400],
            }
        )

    def scale_x(self, value) -> int:
        return self.scale_axis(value, self.screen.get("width"), self.screen.get("inputWidth"))

    def scale_y(self, value) -> int:
        return self.scale_axis(value, self.screen.get("height"), self.screen.get("inputHeight"))

    @staticmethod
    def scale_axis(value, frame_size, input_size) -> int:
        try:
            value = float(value)
            frame_size = float(frame_size or 0)
            input_size = float(input_size or 0)
        except Exception:
            return 0
        if frame_size <= 0 or input_size <= 0:
            return max(0, int(value))
        return max(0, int(round(value * input_size / frame_size)))

    def handle_input(self, msg: dict):
        action = str(msg.get("action") or "")
        duration = max(0.01, min(5.0, float(msg.get("duration") or 80) / 1000.0))
        x = self.scale_x(msg.get("x", 0))
        y = self.scale_y(msg.get("y", 0))
        x2 = self.scale_x(msg.get("x2", 0))
        y2 = self.scale_y(msg.get("y2", 0))
        button = self.normalize_button(msg.get("button") or "left")

        if platform_name() == "macos" and os.environ.get("BHZN_MAC_INPUT_BRIDGE") == "1":
            self.send_macos_input_bridge(msg, action, x, y, x2, y2, duration, button)
            return

        if action == "tap":
            self.click(x, y, button=button)
        elif action == "doubleTap":
            pyautogui.click(x=x, y=y, clicks=2, interval=0.05, button=button)
        elif action == "rightClick":
            self.click(x, y, button="right")
        elif action in ("swipe", "homeSwipe"):
            self.move_pointer(x, y)
            pyautogui.dragTo(x2, y2, duration=duration, button=button)
        elif action == "dragStart":
            self.enter_fast_frame_mode()
            self.release_drag()
            self.move_pointer(x, y)
            self.mouse_down(button)
            self.dragging = True
            self.drag_button = button
        elif action == "dragMove":
            self.enter_fast_frame_mode()
            if not self.dragging:
                self.mouse_down(button)
                self.dragging = True
                self.drag_button = button
            self.move_pointer(x, y, smooth=True)
        elif action == "dragEnd":
            self.enter_fast_frame_mode()
            if self.dragging:
                self.move_pointer(x, y, smooth=True)
                self.mouse_up(self.drag_button)
            else:
                self.move_pointer(x, y)
                self.mouse_up(button)
            self.dragging = False
            self.drag_button = "left"
        elif action == "scroll":
            delta_y = int(msg.get("deltaY") or 0)
            pyautogui.scroll(-max(-10, min(10, delta_y)))
        elif action == "back":
            self.hotkey("alt", "left") if platform_name() == "windows" else self.hotkey("command", "[")
        elif action == "home":
            self.press_home()
        elif action == "key":
            key = str(msg.get("key") or "")
            modifiers = [str(item).lower() for item in msg.get("modifiers") or []]
            if key:
                self.hotkey(*modifiers, self.normalize_key(key)) if modifiers else pyautogui.press(self.normalize_key(key))
        elif action == "text":
            text = str(msg.get("text") or "")
            if text:
                self.input_text(text)

    def send_macos_input_bridge(self, msg: dict, action: str, x: int, y: int, x2: int, y2: int, duration: float, button: str):
        payload = {
            "type": "input",
            "action": action,
            "x": int(x),
            "y": int(y),
            "x2": int(x2),
            "y2": int(y2),
            "durationMs": int(max(10, min(5000, duration * 1000))),
            "button": button,
            "deltaX": int(msg.get("deltaX") or 0),
            "deltaY": int(msg.get("deltaY") or 0),
            "key": str(msg.get("key") or ""),
            "text": str(msg.get("text") or ""),
            "modifiers": [str(item).lower() for item in msg.get("modifiers") or []],
        }
        print("__BHZN_INPUT__" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)

    @staticmethod
    def normalize_button(value: str) -> str:
        value = str(value or "left").lower()
        if value in ("left", "right", "middle"):
            return value
        return "left"

    @staticmethod
    def normalize_key(value: str) -> str:
        mapping = {
            " ": "space",
            "esc": "escape",
            "escape": "escape",
            "enter": "enter",
            "return": "enter",
            "backspace": "backspace",
            "delete": "delete",
            "arrowup": "up",
            "arrowdown": "down",
            "arrowleft": "left",
            "arrowright": "right",
            "meta": "win",
            "command": "command",
            "cmd": "command",
            "control": "ctrl",
            "ctrl": "ctrl",
            "alt": "alt",
            "shift": "shift",
            "tab": "tab",
        }
        key = str(value or "").lower()
        return mapping.get(key, key)

    def hotkey(self, *keys: str):
        keys = [self.normalize_key(key) for key in keys if key]
        if keys:
            pyautogui.hotkey(*keys)

    def enter_fast_frame_mode(self):
        self.fast_frame_until = max(self.fast_frame_until, time.time() + DRAG_FAST_MODE_SECONDS)

    def absolute_point(self, x: int, y: int) -> tuple[int, int]:
        return int(self.input_origin.get("x", 0)) + int(x), int(self.input_origin.get("y", 0)) + int(y)

    def move_pointer(self, x: int, y: int, smooth: bool = False):
        ax, ay = self.absolute_point(x, y)
        if platform_name() == "windows":
            if smooth and self.pointer_pos:
                px, py = self.pointer_pos
                distance = max(abs(ax - px), abs(ay - py))
                steps = max(1, min(6, int(distance / 18)))
                for step in range(1, steps + 1):
                    nx = int(round(px + (ax - px) * step / steps))
                    ny = int(round(py + (ay - py) * step / steps))
                    ctypes.windll.user32.SetCursorPos(nx, ny)
                    if steps > 1:
                        time.sleep(0.001)
            else:
                ctypes.windll.user32.SetCursorPos(int(ax), int(ay))
        else:
            pyautogui.moveTo(ax, ay, duration=0)
        self.pointer_pos = (int(ax), int(ay))

    def mouse_down(self, button: str = "left"):
        if platform_name() == "windows":
            ctypes.windll.user32.mouse_event(self.mouse_event_flag(button, "down"), 0, 0, 0, 0)
        else:
            pyautogui.mouseDown(button=button)

    def mouse_up(self, button: str = "left"):
        if platform_name() == "windows":
            ctypes.windll.user32.mouse_event(self.mouse_event_flag(button, "up"), 0, 0, 0, 0)
        else:
            pyautogui.mouseUp(button=button)

    def click(self, x: int, y: int, button: str = "left"):
        self.move_pointer(x, y)
        self.mouse_down(button)
        self.mouse_up(button)

    @staticmethod
    def mouse_event_flag(button: str, phase: str) -> int:
        flags = {
            ("left", "down"): 0x0002,
            ("left", "up"): 0x0004,
            ("right", "down"): 0x0008,
            ("right", "up"): 0x0010,
            ("middle", "down"): 0x0020,
            ("middle", "up"): 0x0040,
        }
        return flags.get((button, phase), flags[("left", phase)])

    def release_drag(self):
        if self.dragging:
            try:
                self.mouse_up(self.drag_button)
            except Exception:
                pass
        self.dragging = False
        self.drag_button = "left"

    def input_text(self, text: str):
        if not text:
            return
        try:
            old_clipboard = pyperclip.paste()
        except Exception:
            old_clipboard = None
        try:
            pyperclip.copy(text)
            if platform_name() == "macos":
                self.hotkey("command", "v")
            else:
                self.hotkey("ctrl", "v")
            time.sleep(0.02)
        except Exception:
            pyautogui.write(text, interval=0)
        finally:
            if old_clipboard is not None:
                try:
                    pyperclip.copy(old_clipboard)
                except Exception:
                    pass

    def press_home(self):
        if platform_name() == "windows":
            pyautogui.press("win")
        elif platform_name() == "macos":
            self.hotkey("command", "space")
        else:
            pyautogui.press("home")


def parse_args(argv):
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--server", default="", help="Server URL, default production HTTPS endpoint")
    parser.add_argument("--config", default="", help="Custom config file path")
    parser.add_argument("--show-id", action="store_true", help="Print saved device id/code and exit")
    parser.add_argument("--nogui", action="store_true", help="Run without the desktop status window")
    parser.add_argument("--request-screen-permission", action="store_true", help="Trigger one macOS screen capture permission check and exit")
    parser.add_argument("--check-screen-permission", action="store_true", help="Check macOS screen capture permission without prompting")
    parser.add_argument("--request-accessibility-permission", action="store_true", help="Trigger one macOS accessibility permission request and exit")
    parser.add_argument("--check-accessibility-permission", action="store_true", help="Check macOS accessibility permission without prompting")
    return parser.parse_args(argv)


class AgentGui:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.agent = None
        self.agent_thread = None
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("430x360")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.status_var = tk.StringVar(value="未启动")
        self.server_var = tk.StringVar(value=config.server)
        self.id_var = tk.StringVar(value=config.device_id)
        self.code_var = tk.StringVar(value=config.verification_code)

        self.build()
        self.start_agent()
        self.refresh_status()

    def build(self):
        root = self.root
        root.configure(bg="#f4f7fb")
        frame = tk.Frame(root, bg="#ffffff", padx=18, pady=16)
        frame.pack(fill="both", expand=True, padx=18, pady=18)

        tk.Label(frame, text="BHZN ToDesk Agent", bg="#ffffff", fg="#17202a", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        tk.Label(frame, text="Windows/macOS 被控端", bg="#ffffff", fg="#667586").pack(anchor="w", pady=(2, 14))

        self.row(frame, "服务器", self.server_var.get())
        self.row(frame, "设备 ID", self.id_var.get(), copy_value=self.id_var.get())
        self.row(frame, "验证码", self.code_var.get(), copy_value=self.code_var.get())

        status_line = tk.Frame(frame, bg="#ffffff")
        status_line.pack(fill="x", pady=(12, 4))
        tk.Label(status_line, text="状态", bg="#ffffff", fg="#667586", width=10, anchor="w").pack(side="left")
        tk.Label(status_line, textvariable=self.status_var, bg="#ffffff", fg="#1677ff", font=("Segoe UI", 10, "bold")).pack(side="left")

        button_line = tk.Frame(frame, bg="#ffffff")
        button_line.pack(fill="x", pady=(18, 8))
        tk.Button(button_line, text="复制设备 ID", command=lambda: self.copy(self.id_var.get()), width=14).pack(side="left", padx=(0, 8))
        tk.Button(button_line, text="复制验证码", command=lambda: self.copy(self.code_var.get()), width=14).pack(side="left", padx=(0, 8))
        tk.Button(button_line, text="重启连接", command=self.restart_agent, width=12).pack(side="left")

        tk.Label(
            frame,
            text="把设备 ID 和验证码填到 H5 控制台即可绑定。macOS 需要给本程序或 Python 授权 Screen Recording、Accessibility、Input Monitoring。",
            bg="#ffffff",
            fg="#667586",
            wraplength=360,
            justify="left",
        ).pack(anchor="w", pady=(12, 0))

    def row(self, parent, label, value, copy_value=None):
        line = tk.Frame(parent, bg="#ffffff")
        line.pack(fill="x", pady=5)
        tk.Label(line, text=label, bg="#ffffff", fg="#667586", width=10, anchor="w").pack(side="left")
        tk.Label(line, text=value, bg="#eef3f8", fg="#17202a", anchor="w", padx=8, pady=5).pack(side="left", fill="x", expand=True)
        if copy_value:
            tk.Button(line, text="复制", command=lambda: self.copy(copy_value), width=6).pack(side="left", padx=(8, 0))

    def copy(self, value):
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.root.update_idletasks()

    def start_agent(self):
        if self.agent and self.agent.running:
            return
        self.agent = DesktopAgent(self.config)
        self.agent_thread = threading.Thread(target=self.agent.run, daemon=True)
        self.agent_thread.start()

    def restart_agent(self):
        if self.agent:
            self.agent.stop()
        time.sleep(0.2)
        self.start_agent()

    def refresh_status(self):
        if self.agent and self.agent.connected:
            self.status_var.set("在线，正在等待 H5 控制")
        elif self.agent and self.agent.running:
            self.status_var.set("连接中")
        else:
            self.status_var.set("已停止")
        self.root.after(1000, self.refresh_status)

    def on_close(self):
        if messagebox.askokcancel("退出", "退出后这台电脑会离线，确认退出？"):
            if self.agent:
                self.agent.stop()
            self.root.destroy()

    def run(self):
        self.root.mainloop()


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    config = AgentConfig.load(args.config)
    if args.server:
        config.server = normalize_server_url(args.server)
        config.save()
    print(f"{APP_NAME} {AGENT_VERSION}", flush=True)
    print(f"设备 ID: {config.device_id}", flush=True)
    print(f"验证码: {config.verification_code}", flush=True)
    print(f"服务器: {config.server}", flush=True)
    print(f"配置文件: {config.path}", flush=True)
    if args.show_id:
        return 0
    if args.check_screen_permission:
        if platform_name() != "macos" or Quartz is None or not hasattr(Quartz, "CGPreflightScreenCaptureAccess"):
            print("Screen capture permission check is not available.", flush=True)
            return 1
        granted = bool(Quartz.CGPreflightScreenCaptureAccess())
        print("Screen capture permission is available." if granted else "Screen capture permission is not granted.", flush=True)
        return 0 if granted else 1
    if args.request_screen_permission:
        if platform_name() == "macos" and Quartz is not None and hasattr(Quartz, "CGRequestScreenCaptureAccess"):
            granted = bool(Quartz.CGRequestScreenCaptureAccess())
            print("Screen capture permission is available." if granted else "Screen capture permission request was opened.", flush=True)
            return 0 if granted else 1
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                sct.grab(monitor)
            print("Screen capture permission is available.", flush=True)
            return 0
        except Exception as exc:
            print(f"Screen capture permission check failed: {exc}", flush=True)
            return 1
    if args.check_accessibility_permission:
        granted = accessibility_permission_available()
        print("Accessibility permission is available." if granted else "Accessibility permission is not granted.", flush=True)
        return 0 if granted else 1
    if args.request_accessibility_permission:
        granted = request_accessibility_permission()
        print("Accessibility permission is available." if granted else "Accessibility permission request was opened.", flush=True)
        return 0 if granted else 1
    if not args.nogui and tk is not None:
        gui = AgentGui(config)
        gui.run()
        return 0
    if not args.nogui:
        print("GUI is not available on this Python runtime, running in background mode.", flush=True)
    agent = DesktopAgent(config)
    agent.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

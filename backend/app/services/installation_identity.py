"""Persist the installation slot + device seed in a file.

The slot used to live in WebView localStorage, which is tied to
``ROOT/browser_data/desktop_webview``.  OEM upgrades can switch ROOT, which
made the client generate a new slot.  The file below lives under the install
directory (``data/`` is protected by the OTA updater) and is the only source
of truth after the first migration.
"""
from __future__ import annotations

import json
import logging
import secrets
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CLIENT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_FILE = CLIENT_ROOT / "data" / "installation_identity.json"
INSTALLATION_IDENTITY_FILE = Path(
    os.environ.get("LOBSTER_INSTALLATION_IDENTITY_FILE") or str(_DEFAULT_FILE)
)


def _fallback_file() -> Path:
    """机器级备份：安装目录被 OTA 覆盖/无写权限时，仍然能读回原槽位。"""
    override = os.environ.get("LOBSTER_INSTALLATION_IDENTITY_FALLBACK")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "Lobster" / "installation_identity.json"
    return Path.home() / ".lobster" / "installation_identity.json"


def _read_identity_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    installation_id = normalize_identity_value(data.get("installation_id"))
    device_seed = normalize_identity_value(data.get("device_seed"))
    machine_instance_id = normalize_identity_value(data.get("machine_instance_id"))
    legacy_machine_instance_id = merge_legacy_machine_ids(data.get("legacy_machine_instance_id"))
    brand_mark = str(data.get("brand_mark") or "").strip().lower()[:64]
    if not installation_id and not device_seed:
        return None
    if not installation_id:
        installation_id = device_seed
    if not device_seed:
        device_seed = raw_device_seed(installation_id)
    return {
        "present": True,
        "installation_id": installation_id,
        "device_seed": device_seed,
        "machine_instance_id": machine_instance_id,
        "legacy_machine_instance_id": legacy_machine_instance_id,
        "brand_mark": brand_mark,
        "path": str(path),
        "reason": "ok",
    }
_VALID_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_LAST_READ_SIGNATURE: Optional[tuple] = None


def _prefix(value: Any, size: int = 18) -> str:
    text = str(value or "").strip()
    return text[:size]


def normalize_identity_value(value: Any) -> str:
    text = str(value or "").strip()
    return text if _VALID_ID.fullmatch(text) else ""


def merge_legacy_machine_ids(*values: Any, exclude: str = "") -> str:
    """把历次机器 id 合并成逗号列表（去重、剔除当前值）。

    磁盘整盘克隆的机器会算出同一个硬件 id，之后更换算法时新旧 id 都要能认作
    "同一台机器自己"，否则绑定会被判成克隆机而换槽位。
    """
    seen: list[str] = []
    for raw in values:
        for part in re.split(r"[,;\s]+", str(raw or "").strip()):
            item = normalize_identity_value(part)
            if item and item != exclude and item not in seen:
                seen.append(item)
    return ",".join(seen)


def raw_device_seed(value: Any) -> str:
    text = normalize_identity_value(value)
    if "--" in text:
        text = text.split("--", 1)[1]
    # 服务器签发的槽位是 ``u<userId>-<seed>``：seed 必须取后半段，
    # 否则会把整个签名 id 当成 seed 再签一次。
    if re.match(r"^u\d+-", text):
        text = text.split("-", 1)[1]
    return normalize_identity_value(text)


def _empty(path: Optional[Path] = None, reason: str = "") -> Dict[str, Any]:
    return {
        "present": False,
        "installation_id": "",
        "device_seed": "",
        "machine_instance_id": "",
        "legacy_machine_instance_id": "",
        "brand_mark": "",
        "path": str(path or INSTALLATION_IDENTITY_FILE),
        "reason": reason,
    }


def _log_read(result: Dict[str, Any], reason: str) -> None:
    global _LAST_READ_SIGNATURE
    signature = (
        bool(result.get("present")),
        str(result.get("installation_id") or ""),
        str(result.get("device_seed") or ""),
        str(reason or ""),
    )
    if signature == _LAST_READ_SIGNATURE:
        return
    _LAST_READ_SIGNATURE = signature
    if result.get("present"):
        logger.info(
            "[installation-identity] read present=True iid=%s seed_prefix=%s machine=%s brand=%s path=%s reason=%s",
            result.get("installation_id") or "",
            _prefix(result.get("device_seed"), 12),
            _prefix(result.get("machine_instance_id"), 20) or "<empty>",
            str(result.get("brand_mark") or "<empty>"),
            result.get("path") or "",
            reason or "ok",
        )
    else:
        logger.info(
            "[installation-identity] read present=False path=%s reason=%s",
            result.get("path") or "",
            reason or "missing",
        )


def read_installation_identity() -> Dict[str, Any]:
    primary = Path(INSTALLATION_IDENTITY_FILE)
    result = _read_identity_file(primary)
    if result is not None:
        _log_read(result, "ok")
        return result
    fallback = _fallback_file()
    result = _read_identity_file(fallback)
    if result is not None:
        logger.info("[installation-identity] primary missing, using fallback path=%s", fallback)
        _log_read(result, "fallback")
        return result
    result = _empty(primary, "file_missing")
    _log_read(result, "file_missing")
    return result


def ensure_installation_identity(brand_mark: str = "", candidate: Any = "") -> Dict[str, Any]:
    """文件是唯一事实来源：文件没有就当场创建并落盘，绝不返回内存里的临时值。"""
    existing = read_installation_identity()
    brand = str(brand_mark or "").strip().lower()[:64]
    if existing.get("present") and existing.get("installation_id") and existing.get("device_seed"):
        if brand and brand != str(existing.get("brand_mark") or ""):
            updated = _write_identity_files(
                existing["installation_id"],
                existing["device_seed"],
                existing.get("machine_instance_id") or "",
                brand,
            )
            if updated:
                return updated
        return existing
    candidate_id = normalize_identity_value(candidate)
    if candidate_id:
        # 旧版本把 id 存在 localStorage：首次落文件时沿用它，避免第一次升级就换槽位。
        iid = candidate_id
        seed = raw_device_seed(candidate_id) or candidate_id
    else:
        seed = secrets.token_hex(16)
        iid = seed
    updated = _write_identity_files(iid, seed, existing.get("machine_instance_id") or "", brand)
    if updated is None:
        raise OSError("unable to create installation identity file")
    return updated


def _write_identity_files(iid: str, seed: str, machine_id: str, brand_mark: str = "", legacy_machine_id: str = "") -> Optional[Dict[str, Any]]:
    """主文件 + 机器级备份都写；主目录无权限时，备份仍能保住槽位。"""
    payload = {
        "version": 1,
        "installation_id": iid,
        "device_seed": seed,
        "machine_instance_id": machine_id or "",
        "legacy_machine_instance_id": legacy_machine_id or "",
        "brand_mark": str(brand_mark or "").strip().lower()[:64],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    written: list[Path] = []
    for path in (Path(INSTALLATION_IDENTITY_FILE), _fallback_file()):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(str(tmp), str(path))
            written.append(path)
        except OSError as exc:
            logger.warning("[installation-identity] write failed iid=%s path=%s error=%s", _prefix(iid), path, exc)
    if not written:
        return None
    return {
        "present": True,
        "installation_id": iid,
        "device_seed": seed,
        "machine_instance_id": machine_id or "",
        "legacy_machine_instance_id": legacy_machine_id or "",
        "brand_mark": str(brand_mark or "").strip().lower()[:64],
        "path": str(written[0]),
        "paths": [str(path) for path in written],
    }


def write_installation_identity(
    installation_id: Any,
    device_seed: Any,
    *,
    source: str = "",
    machine_instance_id: Any = None,
    brand_mark: Any = None,
    force: bool = False,
) -> Dict[str, Any]:
    iid = normalize_identity_value(installation_id)
    seed = normalize_identity_value(device_seed)
    machine_id = normalize_identity_value(machine_instance_id)
    brand = str(brand_mark or "").strip().lower()[:64]
    if not iid and not seed:
        raise ValueError("installation_id or device_seed is required")
    if not iid:
        iid = seed
    if not seed:
        seed = raw_device_seed(iid)
    if not seed:
        raise ValueError("invalid installation_id/device_seed")

    # First-launch race guard: a second page/profile may load before the first
    # write completes and generate its own unsigned seed.  Once a file exists,
    # only a server-signed slot (u<digits>-...) may replace it.  Unsigned values
    # are ignored and the caller receives the file's authoritative identity.
    existing = read_installation_identity()
    incoming_signed = bool(re.match(r"^u\d+-", iid))
    existing_iid = str(existing.get("installation_id") or "").strip()
    existing_user = re.match(r"^u(\d+)-", existing_iid)
    incoming_user = re.match(r"^u(\d+)-", iid)
    keep_existing = False
    keep_reason = ""
    if existing.get("present") and not incoming_signed:
        keep_existing = True
        keep_reason = "incoming_unsigned"
    elif (
        existing.get("present")
        and existing_user
        and incoming_user
        and existing_user.group(1) == incoming_user.group(1)
    ):
        # 同一个用户下，槽位一旦落文件就不再跟着 machine_id/brand/seed 变化。
        # 服务器重新签发（换机器指纹、换品牌前缀、OTA 后重建 seed）也不能覆盖，
        # 否则就是用户看到的“固化后槽位又变了”。换账号（u<id> 不同）仍允许更新。
        keep_existing = True
        keep_reason = "same_user_signed"
    if force:
        # 只有“用户在系统设置里手动恢复某个别名”这种显式动作才允许覆盖已固化的槽位。
        logger.info(
            "[installation-identity] forced write source=%s incoming_iid=%s existing_iid=%s",
            source or "unknown",
            _prefix(iid),
            existing_iid,
        )
        keep_existing = False
        keep_reason = "forced"
    if keep_existing:
        logger.info(
            "[installation-identity] write kept_slot source=%s reason=%s incoming_iid=%s existing_iid=%s path=%s",
            source or "unknown",
            keep_reason,
            _prefix(iid),
            existing_iid,
            existing.get("path") or "",
        )
        # 槽位保持不变，但机器身份还是要补写，否则换槽位后 known_machine 对不上。
        need_machine = bool(machine_id) and machine_id != str(existing.get("machine_instance_id") or "")
        need_brand = bool(brand) and brand != str(existing.get("brand_mark") or "")
        if need_machine or need_brand:
            updated = _write_identity_files(
                existing_iid,
                raw_device_seed(existing_iid) or normalize_identity_value(existing.get("device_seed")),
                machine_id or str(existing.get("machine_instance_id") or ""),
                brand or str(existing.get("brand_mark") or ""),
                merge_legacy_machine_ids(
                    existing.get("machine_instance_id"),
                    existing.get("legacy_machine_instance_id"),
                    exclude=str(machine_id or ""),
                ),
            )
            if updated:
                logger.info(
                    "[installation-identity] machine refreshed source=%s iid=%s machine=%s legacy=%s",
                    source or "unknown",
                    _prefix(existing_iid),
                    _prefix(updated.get("machine_instance_id"), 20) or "<empty>",
                    _prefix(updated.get("legacy_machine_instance_id"), 20) or "<empty>",
                )
                return {**updated, "ignored": True}
        return {**existing, "ignored": True}

    existing_machine = normalize_identity_value(existing.get("machine_instance_id"))
    resolved_machine = machine_id or existing_machine
    legacy_machine = normalize_identity_value(existing.get("legacy_machine_instance_id"))
    if existing_machine and resolved_machine and existing_machine != resolved_machine:
        legacy_machine = merge_legacy_machine_ids(
            existing_machine,
            existing.get("legacy_machine_instance_id"),
            exclude=resolved_machine,
        )
    updated = _write_identity_files(
        iid,
        seed,
        resolved_machine,
        brand or str(existing.get("brand_mark") or ""),
        legacy_machine,
    )
    if not updated:
        raise OSError("unable to persist installation identity")
    logger.info(
        "[installation-identity] write ok source=%s iid=%s seed_prefix=%s signed=%s machine=%s brand=%s path=%s",
        source or "unknown",
        iid,
        _prefix(seed, 12),
        incoming_signed,
        _prefix(updated.get("machine_instance_id"), 20) or "<empty>",
        str(updated.get("brand_mark") or "<empty>"),
        updated.get("path"),
    )
    return updated

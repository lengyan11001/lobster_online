from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from .assets import ASSETS_DIR, _save_bytes_or_tos
from .auth import _ServerUser, get_current_user_for_local
from ..db import get_db
from ..models import Asset
from ..services.media_edit_exec import find_ffmpeg

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/assets/extract-audio", summary="Extract audio locally and upload it as an asset")
async def extract_audio_asset(
    request: Request,
    file: Optional[UploadFile] = File(None),
    asset_id: str = Form(""),
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    work = Path(tempfile.mkdtemp(prefix="cutcli_audio_"))
    src: Optional[Path] = None
    source_filename = ""
    source_size = 0
    out = work / "audio.wav"
    try:
        aid = (asset_id or "").strip()
        if aid:
            asset = db.query(Asset).filter(Asset.asset_id == aid, Asset.user_id == current_user.id).first()
            if not asset:
                raise HTTPException(404, detail="asset not found")
            if (asset.media_type or "").lower() != "video":
                raise HTTPException(400, detail="asset is not a video")
            filename = asset.filename or ""
            if "/" in filename or "\\" in filename:
                raise HTTPException(400, detail="asset has no local file")
            local = ASSETS_DIR / filename
            if not local.exists():
                raise HTTPException(400, detail="asset local file is missing")
            src = local
            source_filename = filename
            source_size = int(asset.file_size or local.stat().st_size or 0)
        elif file is not None:
            data = await file.read()
            if not data:
                raise HTTPException(400, detail="file is empty")
            src_ext = Path(file.filename or "source.mp4").suffix.lower()
            if src_ext not in (".mp4", ".webm", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".m4v"):
                src_ext = ".mp4"
            src = work / f"source{src_ext}"
            src.write_bytes(data)
            source_filename = file.filename or "source.mp4"
            source_size = len(data)
        else:
            raise HTTPException(400, detail="file or asset_id is required")

        ffmpeg = find_ffmpeg()
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(src),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-acodec",
                "pcm_s16le",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
        if proc.returncode != 0 or not out.exists() or out.stat().st_size <= 0:
            err = (proc.stderr or proc.stdout or "").strip()[:1200]
            raise HTTPException(500, detail=f"ffmpeg extract audio failed: {err}")

        audio_data = out.read_bytes()
        aid, fname, fsize, tos_url = _save_bytes_or_tos(audio_data, ".wav", "audio/wav")
        if not tos_url:
            local_path = ASSETS_DIR / fname
            try:
                if local_path.exists():
                    local_path.unlink()
            except Exception as exc:
                logger.warning("[cutcli-audio] cleanup failed asset_id=%s path=%s err=%s", aid, local_path, exc)
            raise HTTPException(503, detail="audio extracted locally but no public upload URL is available")

        asset = Asset(
            asset_id=aid,
            user_id=current_user.id,
            filename=fname,
            media_type="audio",
            file_size=fsize,
            source_url=tos_url,
            prompt=f"cutcli local audio extract | {source_filename or 'source.mp4'}",
            model="local:ffmpeg-extract-audio",
            tags="cutcli_template,local_audio_extract",
            meta={
                "source_asset_id": (asset_id or "").strip(),
                "source_filename": source_filename,
                "source_size": source_size,
                "ffmpeg_mode": "wav_mono_16k",
                "created_at": int(time.time()),
            },
        )
        db.add(asset)
        db.commit()
        return {
            "ok": True,
            "asset_id": aid,
            "filename": fname,
            "media_type": "audio",
            "file_size": fsize,
            "source_url": tos_url,
            "audio_url": tos_url,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)

import asyncio
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import auth

BASE = Path(__file__).resolve().parent
UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"

UPLOADS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)

app = FastAPI(
    title="Cover KG API",
    version="1.0.0",
    description="Audio, video and cover processing API"
)

# NOTE: allow_credentials must be False when allow_origins is "*" —
# browsers reject that combination outright. The frontend doesn't send
# cookies/credentials, so this is safe. If you later restrict
# allow_origins to specific domains, you can turn credentials back on.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED = {
    ".mp3", ".wav", ".m4a",
    ".mp4", ".mov", ".webm",
    ".jpg", ".jpeg", ".png"
}

# Only ever accept filenames of this exact shape for downloads:
# a 32-char hex job id + a known extension. This closes the
# path-traversal hole (e.g. "../../etc/passwd") that comes from
# passing user input straight into a filesystem path.
SAFE_FILENAME = re.compile(
    r"^[a-f0-9]{32}(\.mp3|\.wav|\.m4a|\.mp4|\.mov|\.webm|\.jpg|\.jpeg|\.png)$"
)

MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB per upload
FFMPEG_TIMEOUT = 300  # seconds — kill runaway/hung ffmpeg jobs
FILE_MAX_AGE_SECONDS = 60 * 60  # delete processed files after 1 hour


async def save_upload_limited(file: UploadFile, dest: Path, max_size: int = MAX_FILE_SIZE):
    """Stream the upload to disk, aborting if it exceeds max_size."""
    size = 0
    with dest.open("wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > max_size:
                buffer.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"Файл өтө чоң (лимит: {max_size // (1024*1024)}MB)"
                )
            buffer.write(chunk)


def run_ffmpeg(command: list[str]):
    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=FFMPEG_TIMEOUT
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="FFmpeg серверге орнотулган эмес")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Иштетүү убакыты өтүп кетти")
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Иштетилген жок: {e.stderr.decode(errors='ignore')[:300]}"
        )


async def cleanup_loop():
    """Background task: periodically remove files older than FILE_MAX_AGE_SECONDS."""
    while True:
        now = time.time()
        for folder in (UPLOADS, OUTPUTS):
            for f in folder.iterdir():
                try:
                    if f.is_file() and (now - f.stat().st_mtime) > FILE_MAX_AGE_SECONDS:
                        f.unlink()
                except FileNotFoundError:
                    pass
        await asyncio.sleep(600)  # check every 10 minutes


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(cleanup_loop())


@app.get("/")
def home():
    return {"name": "Cover KG API", "version": "1.0.0", "status": "online"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/admin/keys")
def admin_create_key(name: str = Form(""), _: None = Depends(auth.require_admin)):
    """Create a new API key. The plaintext key is returned ONCE here —
    save it immediately, it can't be retrieved again afterward."""
    key = auth.create_key(name)
    return {"success": True, "api_key": key, "warning": "Бул ачкычты азыр сактап алыңыз, кайра көрсөтүлбөйт"}


@app.get("/admin/keys")
def admin_list_keys(_: None = Depends(auth.require_admin)):
    return {"keys": auth.list_keys()}


@app.delete("/admin/keys/{key_id}")
def admin_revoke_key(key_id: str, _: None = Depends(auth.require_admin)):
    ok = auth.revoke_key(key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Ачкыч табылган жок")
    return {"success": True, "revoked": key_id}


@app.post("/upload")
async def upload(file: UploadFile = File(...), _: str = Depends(auth.require_api_key)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(status_code=400, detail="Бул файл форматы колдоого алынбайт")

    file_id = uuid.uuid4().hex
    filename = file_id + ext
    path = UPLOADS / filename

    await save_upload_limited(file, path)

    return {
        "success": True,
        "id": file_id,
        "filename": filename,
        "type": file.content_type,
        "download": f"/download/{filename}"
    }


@app.post("/audio/convert")
async def audio_convert(
    file: UploadFile = File(...),
    tempo: float = Form(1.0),
    pitch: float = Form(0),
    _: str = Depends(auth.require_api_key)
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".mp3", ".wav", ".m4a"}:
        raise HTTPException(status_code=400, detail="MP3/WAV/M4A гана")

    job = uuid.uuid4().hex
    source = UPLOADS / f"{job}{ext}"
    output = OUTPUTS / f"{job}.mp3"

    await save_upload_limited(file, source)

    tempo = max(0.5, min(2.0, tempo))
    pitch = max(-12.0, min(12.0, pitch))  # clamp to +/- 1 octave

    filters = []

    if pitch != 0:
        # Pitch shift via resample trick: changing the sample rate shifts
        # pitch but also changes speed, so we correct speed back with atempo.
        # This is an approximation, not a true pitch-preserving shift
        # (that needs the rubberband filter, which isn't in stock ffmpeg
        # builds) — good enough for a first version.
        pitch_factor = 2 ** (pitch / 12)
        new_rate = int(44100 * pitch_factor)
        filters.append(f"asetrate={new_rate}")
        filters.append("aresample=44100")
        # correct for the speed change asetrate introduced, then apply
        # the user's requested tempo on top
        filters.append(f"atempo={tempo / pitch_factor}")
    else:
        filters.append(f"atempo={tempo}")

    command = [
        "ffmpeg", "-y",
        "-i", str(source),
        "-filter:a", ",".join(filters),
        "-codec:a", "libmp3lame",
        "-b:a", "192k",
        str(output)
    ]

    run_ffmpeg(command)

    return {"success": True, "job": job, "file": f"/download/{output.name}"}


@app.post("/video/convert")
async def video_convert(
    file: UploadFile = File(...),
    width: int = Form(1080),
    height: int = Form(1920),
    _: str = Depends(auth.require_api_key)
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".mp4", ".mov", ".webm"}:
        raise HTTPException(status_code=400, detail="Видео форматы туура эмес")

    width = max(64, min(4096, width))
    height = max(64, min(4096, height))

    job = uuid.uuid4().hex
    source = UPLOADS / f"{job}{ext}"
    output = OUTPUTS / f"{job}.mp4"

    await save_upload_limited(file, source)

    command = [
        "ffmpeg", "-y",
        "-i", str(source),
        "-vf",
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(output)
    ]

    run_ffmpeg(command)

    return {"success": True, "job": job, "file": f"/download/{output.name}"}


@app.get("/download/{filename}")
def download(filename: str):
    if not SAFE_FILENAME.match(filename):
        raise HTTPException(status_code=400, detail="Туура эмес файл аты")

    for folder in (UPLOADS, OUTPUTS):
        path = folder / filename
        if path.exists():
            return FileResponse(path, filename=filename)

    raise HTTPException(status_code=404, detail="Файл табылган жок")

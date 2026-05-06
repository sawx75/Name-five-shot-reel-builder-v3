#!/usr/bin/env python3
import json
import re
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
UPLOAD_DIR = APP_DIR / "uploads"
OUTPUT_DIR = APP_DIR / "output_reels"
MUSIC_DIR = APP_DIR / "music"

for d in [STATIC_DIR, UPLOAD_DIR, OUTPUT_DIR, MUSIC_DIR]:
    d.mkdir(exist_ok=True)

app = FastAPI(title="5-Shot Mobile Reel Builder")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/output_reels", StaticFiles(directory=OUTPUT_DIR), name="output_reels")

DEFAULT_TEMPLATE = {
    "label": "WMHSTV 5-Shot Reel",
    "music": "Cake Crumbs.mp3",
    "clip_duration": 2.0,
    "transition_duration": 0.35,
    "vf": "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30,format=yuv420p",
    "crf": "23"
}

def slugify_name(name: str) -> str:
    name = (name or "producer").strip()
    name = re.sub(r"[^A-Za-z0-9 _-]+", "", name)
    name = re.sub(r"\s+", "_", name)
    return name or "producer"

def ffmpeg_path() -> str:
    for p in [shutil.which("ffmpeg"), "/usr/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]:
        if p and Path(p).exists():
            return str(p)
    raise HTTPException(status_code=500, detail="FFmpeg not found. Install FFmpeg first.")

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")

@app.post("/api/assemble")
async def assemble(
    clips: List[UploadFile] = File(...),
    starts_json: str = Form(...),
    producer_name: str = Form(...)
):
    if len(clips) != 5:
        raise HTTPException(status_code=400, detail="Exactly 5 clips are required.")

    try:
        starts = json.loads(starts_json)
    except Exception:
        raise HTTPException(status_code=400, detail="Bad starts_json.")

    if len(starts) != 5:
        raise HTTPException(status_code=400, detail="Need 5 start times.")

    ffmpeg = ffmpeg_path()
    template = DEFAULT_TEMPLATE
    job = uuid.uuid4().hex
    job_dir = UPLOAD_DIR / job
    job_dir.mkdir(exist_ok=True)

    segment_paths = []
    clip_duration = float(template["clip_duration"])

    for i, clip in enumerate(clips):
        suffix = Path(clip.filename or f"clip{i+1}.mov").suffix or ".mov"
        src = job_dir / f"source_{i+1}{suffix}"
        seg = job_dir / f"segment_{i+1}.mp4"

        with src.open("wb") as f:
            shutil.copyfileobj(clip.file, f)

        cmd = [
            ffmpeg, "-y",
            "-ss", str(max(0, float(starts[i]))),
            "-t", str(clip_duration),
            "-i", str(src),
            "-vf", template["vf"],
            "-an",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", template["crf"],
            "-movflags", "+faststart",
            str(seg)
        ]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Clip {i+1} failed: {result.stderr[-1500:]}")

        segment_paths.append(seg)

    date_str = datetime.now().strftime("%Y-%m-%d")
    producer_slug = slugify_name(producer_name)
    out = OUTPUT_DIR / f"{producer_slug}_{date_str}.mp4"

    counter = 2
    while out.exists():
        out = OUTPUT_DIR / f"{producer_slug}_{date_str}_{counter}.mp4"
        counter += 1

    silent_reel = job_dir / "silent_crossfade_reel.mp4"
    transition = float(template["transition_duration"])

    # Five 2-second clips with 0.35s crossfades:
    # final duration = 10 - (4 * 0.35) = 8.6 seconds.
    # Add fade up from black at beginning and a very short fade out at end.
    offsets = [
        clip_duration - transition,
        (clip_duration - transition) * 2,
        (clip_duration - transition) * 3,
        (clip_duration - transition) * 4
    ]
    final_duration = (clip_duration * 5) - (transition * 4)
    fade_out_start = max(0, final_duration - 0.35)

    filter_complex = (
        f"[0:v][1:v]xfade=transition=fade:duration={transition}:offset={offsets[0]}[v01];"
        f"[v01][2:v]xfade=transition=fade:duration={transition}:offset={offsets[1]}[v02];"
        f"[v02][3:v]xfade=transition=fade:duration={transition}:offset={offsets[2]}[v03];"
        f"[v03][4:v]xfade=transition=fade:duration={transition}:offset={offsets[3]},"
        f"fade=t=in:st=0:d=0.5,"
        f"fade=t=out:st={fade_out_start}:d=0.35,"
        f"format=yuv420p[v]"
    )

    xfade_cmd = [ffmpeg, "-y"]
    for seg in segment_paths:
        xfade_cmd += ["-i", str(seg)]

    xfade_cmd += [
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", template["crf"],
        "-movflags", "+faststart",
        str(silent_reel)
    ]

    result = subprocess.run(xfade_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Crossfade assembly failed: {result.stderr[-2000:]}")

    music = MUSIC_DIR / template["music"]

    if music.exists():
        final_cmd = [
            ffmpeg, "-y",
            "-i", str(silent_reel),
            "-stream_loop", "-1",
            "-i", str(music),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-af", f"afade=t=in:st=0:d=0.5,afade=t=out:st={fade_out_start}:d=0.35",
            "-shortest",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(out)
        ]
    else:
        final_cmd = [
            ffmpeg, "-y",
            "-i", str(silent_reel),
            "-c:v", "copy",
            "-an",
            "-movflags", "+faststart",
            str(out)
        ]

    result = subprocess.run(final_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Music/final export failed: {result.stderr[-2000:]}")

    try:
        shutil.rmtree(job_dir)
    except Exception:
        pass

    return {
        "ok": True,
        "filename": out.name,
        "preview_url": f"/output_reels/{out.name}",
        "download_url": f"/output_reels/{out.name}"
    }

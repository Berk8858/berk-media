"""
Berk Media — Film arama ve indirme sistemi
fullhdfilmizlesene.mx + filmmakinesi.to
"""

import os
import re
import json
import hashlib
import asyncio
import subprocess
from pathlib import Path
from typing import Optional
from datetime import datetime

import httpx
from fastapi import FastAPI, Request, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Berk Media", docs_url=None, redoc_url=None)

MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", "/mnt/3tb/Medya"))
TEMP_DIR = Path("/tmp/berk-media")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}

active_downloads: dict[str, dict] = {}


def get_media_dirs() -> list[dict]:
    dirs = []
    if MEDIA_ROOT.exists():
        for item in sorted(MEDIA_ROOT.iterdir()):
            if item.is_dir() and not item.name.startswith("."):
                dirs.append({"name": item.name, "path": str(item)})
    return dirs


def vtt_to_srt(vtt_content: str) -> str:
    lines = vtt_content.strip().split("\n")
    srt_lines = []
    idx = 0
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            idx += 1
            srt_lines.append(str(idx))
            srt_lines.append(line.replace(".", ","))
            i += 1
            while i < len(lines) and lines[i].strip() and "-->" not in lines[i]:
                srt_lines.append(lines[i].strip())
                i += 1
            srt_lines.append("")
        else:
            i += 1
    return "\n".join(srt_lines)


async def search_fullhdfilmizlesene(query: str) -> list[dict]:
    async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
        try:
            resp = await client.get(
                "https://www.fullhdfilmizlesene.mx/",
                params={"arama": query}
            )
            if resp.status_code != 200:
                return []
            return parse_search_results(resp.text, "fullhdfilmizlesene")
        except Exception:
            return []


def parse_search_results(html: str, source: str) -> list[dict]:
    results = []
    if source == "fullhdfilmizlesene":
        pattern = r'<a class="tt" href="(https://www\.fullhdfilmizlesene\.mx/film/[^"]+)"[^>]*>([^<]+)</a>.*?<span class="film-title">([^<]*)</span>(?:\s*<span class="kt">([^<]*)</span>)?.*?(?:<span class="imdb">([^<]*)</span>)?.*?<span class="film-yil">(\d{4})</span>'
        for m in re.finditer(pattern, html, re.DOTALL):
            url = m.group(1)
            title_tr = m.group(3).strip() or m.group(2).strip()
            title_en = m.group(4).strip() if m.group(4) else ""
            imdb = m.group(5) if m.group(5) else ""
            year = m.group(6)
            poster_match = re.search(r'data-src="(https://img\.fullhdfilmizlesene\.mx/poster/film/[^"]+\.jpg)"', html[m.start():m.start()+2000])
            poster = poster_match.group(1) if poster_match else ""
            results.append({
                "title": title_tr,
                "original_title": title_en,
                "year": year,
                "imdb": imdb,
                "url": url,
                "poster": poster,
                "source": "fullhdfilmizlesene",
            })
    return results


async def get_film_details(url: str) -> dict:
    async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {}
            return parse_film_details(resp.text, url)
        except Exception:
            return {}


def parse_film_details(html: str, url: str) -> dict:
    details = {"url": url}

    ld_match = re.search(r'<script type="application/ld\+json">\s*({.*?})\s*</script>', html, re.DOTALL)
    if ld_match:
        try:
            ld = json.loads(ld_match.group(1))
            details["title"] = ld.get("name", "")
            details["description"] = ld.get("description", "")
            details["poster"] = ld.get("image", "")
            details["year"] = ""
            if "actor" in ld:
                details["actors"] = [a.get("name", "") for a in ld["actor"][:5]]
            if "director" in ld:
                details["director"] = ld["director"].get("name", "")
            if "aggregateRating" in ld:
                details["imdb"] = str(ld["aggregateRating"].get("ratingValue", ""))
            if "duration" in ld:
                dur = ld["duration"]
                m = re.match(r"PT(\d+)M", dur)
                if m:
                    details["duration"] = f"{m.group(1)} dk"
        except json.JSONDecodeError:
            pass

    if "title" not in details:
        title_match = re.search(r'<h1[^>]*><a[^>]*>([^<]+)</a>', html)
        if title_match:
            details["title"] = title_match.group(1).strip()

    if "poster" not in details or not details["poster"]:
        poster_match = re.search(r'data-src="(https://img\.fullhdfilmizlesene\.mx/poster/izle/[^"]+\.jpg)"', html)
        if poster_match:
            details["poster"] = poster_match.group(1)

    year_match = re.search(r'"datePublished":"(\d{4})', html)
    if year_match:
        details["year"] = year_match.group(1)

    vidid_match = re.search(r"var vidid = '(\d+)'", html)
    if vidid_match:
        details["vidid"] = vidid_match.group(1)

    sources = []
    source_pattern = re.findall(r'data-src="(https?://[^"]+\.m3u8[^"]*)"', html)
    if not source_pattern:
        source_pattern = re.findall(r'"(https?://[^"]+\.m3u8[^"]*)"', html)
    for src in source_pattern[:5]:
        sources.append({"url": src, "label": "HLS"})
    details["sources"] = sources

    sub_pattern = re.findall(r'(https?://[^"]+\.vtt[^"]*)', html)
    details["subtitles"] = [{"url": s, "label": "Türkçe"} for s in sub_pattern[:3]]

    return details


async def download_file(url: str, output_path: Path, task_id: str) -> bool:
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=300, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return False
                total = int(resp.headers.get("content-length", 0))
                downloaded = 0
                with open(output_path, "wb") as f:
                    async for chunk in resp.aiter_chunk_size(8192):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if task_id in active_downloads:
                            active_downloads[task_id]["progress"] = (
                                round(downloaded / total * 100, 1) if total else 0
                            )
                return True
    except Exception:
        return False


async def download_hls_stream(hls_url: str, output_path: Path, task_id: str) -> bool:
    try:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", hls_url,
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path)
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if task_id in active_downloads:
            active_downloads[task_id]["status"] = "downloading"
        stdout, stderr = await proc.communicate()
        return proc.returncode == 0
    except Exception:
        return False


async def merge_to_mkv(video_path: Path, audio_paths: list[Path], sub_paths: list[Path],
                       poster_path: Optional[Path], output_path: Path) -> bool:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    cmd.extend(["-i", str(video_path)])
    for ap in audio_paths:
        cmd.extend(["-i", str(ap)])
    for sp in sub_paths:
        cmd.extend(["-i", str(sp)])

    cmd.extend(["-map", "0:v", "-map", "0:a?"])
    for i in range(len(audio_paths)):
        cmd.extend(["-map", str(i + 1)])
    for i in range(len(sub_paths)):
        cmd.extend(["-map", str(i + 1 + len(audio_paths))])

    cmd.extend(["-c:v", "copy", "-c:a", "copy", "-c:s", "srt"])

    if poster_path and poster_path.exists():
        cmd.extend([
            "-map", str(1 + len(audio_paths) + len(sub_paths)) if (audio_paths or sub_paths) else "1",
        ])

    cmd.append(str(output_path))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode == 0


@app.get("/", response_class=HTMLResponse)
async def index():
    return open("/app/templates/index.html").read()


@app.get("/api/dirs")
async def api_dirs():
    return get_media_dirs()


@app.get("/api/search")
async def api_search(q: str = Query(..., min_length=2)):
    results = await search_fullhdfilmizlesene(q)
    return results


@app.get("/api/film")
async def api_film(url: str = Query(...)):
    details = await get_film_details(url)
    return details


@app.get("/api/subtitle/convert")
async def api_convert_subtitle(url: str = Query(...)):
    async with httpx.AsyncClient(headers=HEADERS, timeout=15) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            return {"error": "Altyazı indirilemedi"}
        srt_content = vtt_to_srt(resp.text)
        filename = hashlib.md5(url.encode()).hexdigest()[:8] + ".srt"
        srt_path = TEMP_DIR / filename
        srt_path.write_text(srt_content, encoding="utf-8")
        return {"content": srt_content, "filename": filename}


@app.post("/api/download")
async def api_download(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    film_title = data.get("title", "film")
    video_url = data.get("video_url", "")
    subtitle_urls = data.get("subtitles", [])
    save_dir = data.get("save_dir", str(MEDIA_ROOT / "Movies"))
    poster_url = data.get("poster", "")

    if not video_url:
        return {"error": "Video URL gerekli"}

    task_id = hashlib.md5(f"{film_title}{datetime.now().timestamp()}".encode()).hexdigest()[:12]
    safe_name = re.sub(r'[^\w\s-]', '', film_title).strip().replace(" ", "_")
    task_dir = TEMP_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    active_downloads[task_id] = {
        "title": film_title,
        "status": "starting",
        "progress": 0,
        "save_dir": save_dir,
        "started": datetime.now().isoformat(),
    }

    async def _do_download():
        try:
            video_ext = ".mp4"
            video_path = task_dir / f"video{video_ext}"

            if ".m3u8" in video_url:
                active_downloads[task_id]["status"] = "downloading_hls"
                ok = await download_hls_stream(video_url, video_path, task_id)
            else:
                active_downloads[task_id]["status"] = "downloading"
                ok = await download_file(video_url, video_path, task_id)

            if not ok:
                active_downloads[task_id]["status"] = "error"
                return

            sub_paths = []
            for sub in subtitle_urls:
                sub_url = sub.get("url", "")
                if sub_url:
                    async with httpx.AsyncClient(headers=HEADERS, timeout=15) as client:
                        resp = await client.get(sub_url)
                        if resp.status_code == 200:
                            srt_content = vtt_to_srt(resp.text)
                            sub_path = task_dir / f"sub_{len(sub_paths)}.srt"
                            sub_path.write_text(srt_content, encoding="utf-8")
                            sub_paths.append(sub_path)

            poster_path = None
            if poster_url:
                poster_path = task_dir / "poster.jpg"
                await download_file(poster_url, poster_path, f"{task_id}_poster")

            final_name = f"{safe_name}.mkv"
            final_path = Path(save_dir) / final_name
            final_path.parent.mkdir(parents=True, exist_ok=True)

            if sub_paths:
                active_downloads[task_id]["status"] = "merging"
                ok = await merge_to_mkv(video_path, [], sub_paths, poster_path, final_path)
                if not ok:
                    active_downloads[task_id]["status"] = "merge_error"
                    return
            else:
                import shutil
                shutil.move(str(video_path), str(final_path))

            active_downloads[task_id]["status"] = "completed"
            active_downloads[task_id]["path"] = str(final_path)

        except Exception as e:
            active_downloads[task_id]["status"] = "error"
            active_downloads[task_id]["error"] = str(e)

    background_tasks.add_task(_do_download)

    return {"task_id": task_id, "status": "started"}


@app.get("/api/download/{task_id}")
async def api_download_status(task_id: str):
    if task_id not in active_downloads:
        return {"error": "Görev bulunamadı"}
    return active_downloads[task_id]


@app.get("/api/downloads")
async def api_downloads():
    return active_downloads


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8084)

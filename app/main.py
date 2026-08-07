"""
Berk Media — Film arama ve indirme sistemi
Çoklu kaynak destekli: fullhdfilmizlesene.mx, filmmakinesi.to, ...
"""

import os
import re
import json
import hashlib
import asyncio
import base64
import shutil
from pathlib import Path
from typing import Optional
from datetime import datetime

import httpx
from fastapi import FastAPI, Request, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, Response

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

SOURCES = {
    "fullhdfilmizlesene": {
        "name": "FullHDFilmIzlesene",
        "domain": "fullhdfilmizlesene.mx",
    },
    "filmmakinesi": {
        "name": "FilmMakinesi",
        "domain": "filmmakinesi.to",
    },
}


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


def detect_source(url: str) -> str:
    if "fullhdfilmizlesene" in url:
        return "fullhdfilmizlesene"
    if "filmmakinesi" in url:
        return "filmmakinesi"
    return "unknown"


async def search_all(query: str) -> list[dict]:
    results = []
    async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
        tasks = [
            search_fullhdfilmizlesene(client, query),
        ]
        for coro in asyncio.as_completed(tasks):
            try:
                r = await coro
                results.extend(r)
            except Exception:
                pass
    return results


async def search_fullhdfilmizlesene(client: httpx.AsyncClient, query: str) -> list[dict]:
    try:
        slug = query.lower().replace(" ", "+").replace("ı", "i").replace("ö", "o").replace("ü", "u").replace("ş", "s").replace("ç", "c").replace("ğ", "g")
        resp = await client.get(f"https://www.fullhdfilmizlesene.mx/arama/{slug}")
        if resp.status_code != 200:
            return []
        return parse_fullhdfilmizlesene_results(resp.text)
    except Exception:
        return []


def parse_fullhdfilmizlesene_results(html: str) -> list[dict]:
    results = []
    seen = set()

    links = re.findall(
        r'href="(https://www\.fullhdfilmizlesene\.mx/film/[^"]+)"[^>]*>([^<]*)</a>',
        html
    )
    titles = re.findall(r'<span class="film-title">([^<]+)</span>', html)
    posters = re.findall(r'(?:data-src|src)="(https?://img\.fullhdfilmizlesene\.mx/poster/[^"]+\.(?:jpg|jpeg|png|webp|gif|avif))"', html)
    if len(posters) < len(links):
        more = re.findall(r'(?:data-src|src)="(https?://[^"]+\.(?:jpg|jpeg|png|webp|gif|avif)[^"]*)"', html)
        seen_urls = set(posters)
        for u in more:
            if u not in seen_urls and 'poster' in u.lower():
                posters.append(u)
    years = re.findall(r'<span class="film-yil">(\d{4})</span>', html)
    imdbs = re.findall(r'<span class="imdb">([^<]+)</span>', html)
    qualities = re.findall(r'<span class="uhd">([^<]+)</span>', html)

    for i, (url, raw_title) in enumerate(links):
        if url in seen:
            continue
        seen.add(url)

        title = titles[i].strip() if i < len(titles) else raw_title.strip()
        if not title or title == "izle":
            title = raw_title.replace(" izle", "").strip()

        year = years[i] if i < len(years) else ""
        imdb = imdbs[i] if i < len(imdbs) else ""
        poster = posters[i] if i < len(posters) else ""
        quality = qualities[i] if i < len(qualities) else ""

        results.append({
            "title": title,
            "year": year,
            "imdb": imdb,
            "url": url,
            "poster": poster,
            "quality": quality,
            "source": "fullhdfilmizlesene",
        })

    return results


async def get_film_details(url: str) -> dict:
    source = detect_source(url)
    async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {}
            if source == "fullhdfilmizlesene":
                return parse_fullhdfilmizlesene_details(resp.text, url)
            return {"url": url, "source": source}
        except Exception:
            return {}


def extract_youtube_id_from_html(html: str) -> str:
    m = re.search(r'data-code="([^"]+)"', html)
    if m:
        try:
            decoded = base64.b64decode(m.group(1)).decode("utf-8", errors="ignore")
            yt = re.search(r'youtube\.com/embed/([a-zA-Z0-9_-]+)', decoded)
            if yt:
                return yt.group(1)
        except Exception:
            pass
    yt = re.search(r'youtube\.com/embed/([a-zA-Z0-9_-]+)', html)
    if yt:
        return yt.group(1)
    return ""


def parse_fullhdfilmizlesene_details(html: str, url: str) -> dict:
    details = {"url": url, "source": "fullhdfilmizlesene"}

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
            dur_str = ld.get("duration", "")
            if dur_str:
                m = re.match(r"PT(\d+)M", dur_str)
                if m:
                    details["duration"] = f"{m.group(1)} dk"
        except json.JSONDecodeError:
            pass

    if "title" not in details:
        title_match = re.search(r'<h1[^>]*><a[^>]*>([^<]+)</a>', html)
        if title_match:
            details["title"] = title_match.group(1).strip()

    if "poster" not in details or not details["poster"]:
        poster_match = re.search(r'(?:data-src|src)="(https?://img\.fullhdfilmizlesene\.mx/poster/[^"]+\.(?:jpg|jpeg|png|webp|gif|avif))"', html)
        if poster_match:
            details["poster"] = poster_match.group(1)

    year_match = re.search(r'"datePublished":"(\d{4})', html)
    if year_match:
        details["year"] = year_match.group(1)

    yt_id = extract_youtube_id_from_html(html)
    if yt_id:
        details["youtube_id"] = yt_id
        details["youtube_url"] = f"https://www.youtube.com/watch?v={yt_id}"

    vidid_match = re.search(r"var vidid = '(\d+)'", html)
    if vidid_match:
        details["vidid"] = vidid_match.group(1)

    sub_pattern = re.findall(r'(https?://[^"]+\.vtt[^"]*)', html)
    details["subtitles"] = [{"url": s, "label": "Altyazı"} for s in sub_pattern[:3]]

    return details


async def probe_video(url: str) -> dict:
    cmd = [
        "yt-dlp", "--no-download", "--print-json",
        "--no-playlist",
        url,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return {"error": stderr.decode(errors="ignore")[:500]}

    try:
        info = json.loads(stdout.decode())
    except json.JSONDecodeError:
        return {"error": "JSON parse hatası"}

    formats = info.get("formats", [])

    video_formats = []
    seen_heights = set()
    for f in formats:
        h = f.get("height")
        vcodec = f.get("vcodec", "none")
        if h and vcodec != "none" and h not in seen_heights:
            seen_heights.add(h)
            video_formats.append({
                "height": h,
                "label": f"{h}p",
                "format_id": f.get("format_id", ""),
                "ext": f.get("ext", "mp4"),
            })
    video_formats.sort(key=lambda x: x["height"], reverse=True)

    audio_formats = []
    seen_audio = set()
    for f in formats:
        acodec = f.get("acodec", "none")
        vcodec = f.get("vcodec", "none")
        if acodec != "none" and vcodec == "none":
            lang = f.get("language") or f.get("format_note", "")
            abr = f.get("abr", 0)
            key = lang or f.get("format_id", "")
            if key not in seen_audio:
                seen_audio.add(key)
                audio_formats.append({
                    "language": lang or "Bilinmeyen",
                    "format_id": f.get("format_id", ""),
                    "abr": abr,
                    "label": f"{lang or 'Bilinmeyen'} ({abr}kbps)" if abr else lang or "Bilinmeyen",
                })

    subtitles = []
    for s in info.get("subtitles", {}).keys():
        subtitles.append({"language": s, "label": s})
    for s in info.get("automatic_captions", {}).keys():
        if not any(sub["language"] == s for sub in subtitles):
            subtitles.append({"language": s, "label": f"{s} (otomatik)"})

    return {
        "title": info.get("title", ""),
        "duration": info.get("duration"),
        "duration_string": info.get("duration_string", ""),
        "thumbnail": info.get("thumbnail", ""),
        "video_formats": video_formats,
        "audio_formats": audio_formats,
        "subtitles": subtitles,
        "requested_url": info.get("requested_url", ""),
    }


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


async def download_with_ytdlp(
    url: str,
    output_path: Path,
    task_id: str,
    format_id: str = "",
    audio_lang: str = "",
    sub_langs: list[str] = None,
    output_format: str = "mkv",
) -> bool:
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--newline",
        "--progress",
    ]

    if format_id:
        cmd.extend(["-f", f"{format_id}+bestaudio/best"])
    else:
        cmd.extend(["-f", "bestvideo+bestaudio/best"])

    if audio_lang:
        cmd.extend(["--audio-language", audio_lang])

    if sub_langs:
        cmd.extend(["--sub-langs", ",".join(sub_langs), "--embed-subs"])

    cmd.extend(["--merge-output-format", output_format])
    cmd.extend(["-o", str(output_path), url])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    if task_id in active_downloads:
        active_downloads[task_id]["status"] = "downloading"

    async for line in proc.stdout:
        decoded = line.decode(errors="ignore").strip()
        if "[download]" in decoded and "%" in decoded:
            pct_match = re.search(r'(\d+\.?\d*)%', decoded)
            if pct_match and task_id in active_downloads:
                active_downloads[task_id]["progress"] = float(pct_match.group(1))

    await proc.wait()
    return proc.returncode == 0


@app.get("/", response_class=HTMLResponse)
async def index():
    return open("/app/templates/index.html").read()


@app.get("/api/dirs")
async def api_dirs():
    return get_media_dirs()


@app.get("/api/sources")
async def api_sources():
    return [{"id": k, "name": v["name"], "domain": v["domain"]} for k, v in SOURCES.items()]


@app.get("/api/search")
async def api_search(q: str = Query(..., min_length=2)):
    return await search_all(q)


@app.get("/api/film")
async def api_film(url: str = Query(...)):
    return await get_film_details(url)


@app.get("/api/probe")
async def api_probe(url: str = Query(...)):
    return await probe_video(url)


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


@app.get("/api/poster")
async def api_poster(url: str = Query(...)):
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                ct = resp.headers.get("content-type", "image/jpeg")
                return Response(content=resp.content, media_type=ct)
    except Exception:
        pass
    return Response(status_code=404)


@app.post("/api/download")
async def api_download(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    film_title = data.get("title", "film")
    source_url = data.get("url", "")
    video_url = data.get("video_url", "")
    format_id = data.get("format_id", "")
    audio_lang = data.get("audio_lang", "")
    sub_langs = data.get("sub_langs", [])
    output_format = data.get("output_format", "mkv")
    save_dir = data.get("save_dir", str(MEDIA_ROOT / "Movies"))
    poster_url = data.get("poster", "")

    download_url = video_url or source_url
    if not download_url:
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
            final_ext = f".{output_format}"
            final_name = f"{safe_name}{final_ext}"
            final_path = Path(save_dir) / final_name
            final_path.parent.mkdir(parents=True, exist_ok=True)

            if "youtube.com" in download_url or "youtu.be" in download_url:
                temp_out = task_dir / f"video{final_ext}"
                ok = await download_with_ytdlp(
                    download_url, temp_out, task_id,
                    format_id=format_id,
                    audio_lang=audio_lang,
                    sub_langs=sub_langs,
                    output_format=output_format,
                )
                if not ok:
                    active_downloads[task_id]["status"] = "error"
                    return
                shutil.move(str(temp_out), str(final_path))
            elif ".m3u8" in download_url:
                temp_out = task_dir / f"video.mp4"
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", download_url, "-c", "copy", "-movflags", "+faststart",
                    str(temp_out),
                ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                active_downloads[task_id]["status"] = "downloading"
                await proc.wait()
                if proc.returncode != 0:
                    active_downloads[task_id]["status"] = "error"
                    return
                shutil.move(str(temp_out), str(final_path))
            else:
                temp_out = task_dir / f"video{final_ext}"
                ok = await download_file(download_url, temp_out, task_id)
                if not ok:
                    active_downloads[task_id]["status"] = "error"
                    return
                shutil.move(str(temp_out), str(final_path))

            if poster_url:
                poster_path = task_dir / "poster.jpg"
                await download_file(poster_url, poster_path, f"{task_id}_poster")

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

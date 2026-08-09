"""
Berk Media v1.3 — Film arama ve indirme sistemi
Coklu kaynak destekli: fullhdfilmizlesene.mx, filmmakinesi.to
Cloudflare bypass: curl_cffi ile Chrome taklidi
HLS cikarma: unpack_hls.js ile packed JS cozme
"""

import os
import re
import json
import hashlib
import asyncio
import base64
import shutil
import subprocess
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

from curl_cffi.requests import AsyncSession as CurlSession
from fastapi import FastAPI, Request, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, Response

logger = logging.getLogger("berk-media")

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

app = FastAPI(title="Berk Media", docs_url=None, redoc_url=None)

MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", "/mnt/3tb/Medya"))
TEMP_DIR = Path("/tmp/berk-media")
TEMP_DIR.mkdir(parents=True, exist_ok=True)
UNPACK_SCRIPT = Path("/app/unpack_hls.js")

active_downloads: dict[str, dict] = {}

SOURCES = {
    "filmmakinesi": {
        "name": "FilmMakinesi",
        "domain": "filmmakinesi.to",
    },
    "fullhdfilmizlesene": {
        "name": "FullHDFilmIzlesene",
        "domain": "fullhdfilmizlesene.mx",
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
    if "filmmakinesi" in url:
        return "filmmakinesi"
    if "fullhdfilmizlesene" in url:
        return "fullhdfilmizlesene"
    return "unknown"


async def get_curl_session():
    session = CurlSession(impersonate="chrome124")
    return session


# =============================================================================
# FILMMAKINESI.TO
# =============================================================================

async def search_filmmakinesi(query: str) -> list[dict]:
    try:
        async with CurlSession(impersonate="chrome124") as session:
            slug = query.lower().replace(" ", "+")
            resp = await session.get(f"https://filmmakinesi.to/ara/{slug}")
            if resp.status_code != 200:
                return []
            return parse_filmmakinesi_results(resp.text)
    except Exception as e:
        logger.error(f"filmmakinesi search error: {e}")
        return []


def parse_filmmakinesi_results(html: str) -> list[dict]:
    results = []
    seen = set()

    cards = re.findall(
        r'<a[^>]+href="(https://filmmakinesi\.to/[^"]+)"[^>]*class="[^"]*poster[^"]*"[^>]*>.*?</a>',
        html, re.DOTALL
    )

    if not cards:
        links = re.findall(r'href="(https://filmmakinesi\.to/(?:film|dizi)/[^"]+)"', html)
        posters = re.findall(r'(?:data-src|src)="(https?://[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', html)
        titles_raw = re.findall(r'<h2[^>]*>([^<]+)</h2>', html)
        years = re.findall(r'<span[^>]*class="[^"]*year[^"]*"[^>]*>(\d{4})</span>', html)
        imdbs = re.findall(r'<span[^>]*class="[^"]*imdb[^"]*"[^>]*>([0-9.]+)</span>', html)
        qualities = re.findall(r'<span[^>]*class="[^"]*quality[^"]*"[^>]*>([^<]+)</span>', html)

        for i, url in enumerate(links):
            if url in seen:
                continue
            seen.add(url)
            title = titles_raw[i].strip() if i < len(titles_raw) else url.split("/")[-1].replace("-", " ").title()
            results.append({
                "title": title,
                "year": years[i] if i < len(years) else "",
                "imdb": imdbs[i] if i < len(imdbs) else "",
                "url": url,
                "poster": posters[i] if i < len(posters) else "",
                "quality": qualities[i] if i < len(qualities) else "",
                "source": "filmmakinesi",
            })
    return results


async def get_filmmakinesi_details(url: str) -> dict:
    try:
        async with CurlSession(impersonate="chrome124") as session:
            resp = await session.get(url)
            if resp.status_code != 200:
                return {"url": url, "source": "filmmakinesi"}
            return parse_filmmakinesi_details(resp.text, url)
    except Exception as e:
        logger.error(f"filmmakinesi detail error: {e}")
        return {"url": url, "source": "filmmakinesi"}


def parse_filmmakinesi_details(html: str, url: str) -> dict:
    details = {"url": url, "source": "filmmakinesi"}

    ld_match = re.search(r'<script type="application/ld\+json">\s*({.*?})\s*</script>', html, re.DOTALL)
    if ld_match:
        try:
            ld = json.loads(ld_match.group(1))
            details["title"] = ld.get("name", "")
            details["description"] = ld.get("description", "")
            details["poster"] = ld.get("image", "")
            if "actor" in ld:
                details["actors"] = [a.get("name", "") for a in (ld["actor"] if isinstance(ld["actor"], list) else [ld["actor"]])[:5]]
            if "director" in ld:
                d = ld["director"]
                details["director"] = d.get("name", "") if isinstance(d, dict) else str(d)
            if "aggregateRating" in ld:
                details["imdb"] = str(ld["aggregateRating"].get("ratingValue", ""))
            dur = ld.get("duration", "")
            if dur:
                m = re.match(r"PT(\d+)M", dur)
                if m:
                    details["duration"] = f"{m.group(1)} dk"
        except json.JSONDecodeError:
            pass

    if "title" not in details:
        t = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
        if t:
            details["title"] = t.group(1).strip()

    if "poster" not in details or not details["poster"]:
        p = re.search(r'(?:data-src|src)="(https?://[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', html)
        if p:
            details["poster"] = p.group(1)

    y = re.search(r'"datePublished":"(\d{4})', html)
    if y:
        details["year"] = y.group(1)

    embed = re.search(r'data-src="([^"]*embed[^"]*)"', html)
    if embed:
        details["embed_url"] = embed.group(1)

    yt = re.search(r'youtube\.com/embed/([a-zA-Z0-9_-]+)', html)
    if yt:
        details["youtube_id"] = yt.group(1)
        details["youtube_url"] = f"https://www.youtube.com/watch?v={yt.group(1)}"

    return details


async def extract_hls_from_filmmakinesi(page_url: str, embed_url: str = "") -> dict:
    """Extract HLS URL from filmmakinesi.to using curl_cffi + unpack_hls.js"""
    try:
        async with CurlSession(impersonate="chrome124") as session:
            if not embed_url:
                resp = await session.get(page_url, headers={"Referer": page_url})
                if resp.status_code != 200:
                    return {"error": f"Sayfa yuklenemedi ({resp.status_code})"}
                html = resp.text
                embed_match = re.search(r'data-src="([^"]*embed[^"]*)"', html)
                if not embed_match:
                    yt = re.search(r'youtube\.com/embed/([a-zA-Z0-9_-]+)', html)
                    if yt:
                        return {"video_url": f"https://www.youtube.com/watch?v={yt.group(1)}", "type": "youtube"}
                    return {"error": "Embed URL bulunamadi"}
                embed_url = embed_match.group(1)
                if not embed_url.startswith("http"):
                    embed_url = "https://filmmakinesi.to" + embed_url

            resp2 = await session.get(embed_url, headers={"Referer": page_url})
            if resp2.status_code != 200:
                return {"error": f"Embed sayfa yuklenemedi ({resp2.status_code})"}

            embed_html = resp2.text

            direct_m3u8 = re.search(r'(https?://[^"\'\\s]+\.m3u8[^"\'\\s]*)', embed_html)
            if direct_m3u8:
                return {"video_url": direct_m3u8.group(1), "type": "hls"}

            embed_path = TEMP_DIR / "embed_page.html"
            embed_path.write_text(embed_html, encoding="utf-8")

            if UNPACK_SCRIPT.exists():
                proc = await asyncio.create_subprocess_exec(
                    "node", str(UNPACK_SCRIPT),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
                if proc.returncode == 0:
                    url = stdout.decode().strip()
                    if url.startswith("http"):
                        return {"video_url": url, "type": "hls"}

            return {"error": "HLS URL cikarilamadi"}

    except Exception as e:
        logger.error(f"filmmakinesi extraction error: {e}")
        return {"error": str(e)[:200]}


# =============================================================================
# FULLHDFILMIZLESENE.MX
# =============================================================================

async def search_fullhdfilmizlesene(query: str) -> list[dict]:
    try:
        async with CurlSession(impersonate="chrome124") as session:
            slug = query.lower().replace(" ", "+").replace("ı", "i").replace("ö", "o").replace("ü", "u").replace("ş", "s").replace("ç", "c").replace("ğ", "g")
            resp = await session.get(f"https://www.fullhdfilmizlesene.mx/arama/{slug}")
            if resp.status_code != 200:
                return []
            return parse_fullhdfilmizlesene_results(resp.text)
    except Exception as e:
        logger.error(f"fullhdfilmizlesene search error: {e}")
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
        results.append({
            "title": title,
            "year": years[i] if i < len(years) else "",
            "imdb": imdbs[i] if i < len(imdbs) else "",
            "url": url,
            "poster": posters[i] if i < len(posters) else "",
            "quality": qualities[i] if i < len(qualities) else "",
            "source": "fullhdfilmizlesene",
        })
    return results


async def get_fullhdfilmizlesene_details(url: str) -> dict:
    try:
        async with CurlSession(impersonate="chrome124") as session:
            resp = await session.get(url)
            if resp.status_code != 200:
                return {"url": url, "source": "fullhdfilmizlesene"}
            return parse_fullhdfilmizlesene_details(resp.text, url)
    except Exception as e:
        logger.error(f"fullhdfilmizlesene detail error: {e}")
        return {"url": url, "source": "fullhdfilmizlesene"}


def parse_fullhdfilmizlesene_details(html: str, url: str) -> dict:
    details = {"url": url, "source": "fullhdfilmizlesene"}

    ld_match = re.search(r'<script type="application/ld\+json">\s*({.*?})\s*</script>', html, re.DOTALL)
    if ld_match:
        try:
            ld = json.loads(ld_match.group(1))
            details["title"] = ld.get("name", "")
            details["description"] = ld.get("description", "")
            details["poster"] = ld.get("image", "")
            if "actor" in ld:
                details["actors"] = [a.get("name", "") for a in (ld["actor"] if isinstance(ld["actor"], list) else [ld["actor"]])[:5]]
            if "director" in ld:
                d = ld["director"]
                details["director"] = d.get("name", "") if isinstance(d, dict) else str(d)
            if "aggregateRating" in ld:
                details["imdb"] = str(ld["aggregateRating"].get("ratingValue", ""))
            dur = ld.get("duration", "")
            if dur:
                m = re.match(r"PT(\d+)M", dur)
                if m:
                    details["duration"] = f"{m.group(1)} dk"
        except json.JSONDecodeError:
            pass

    if "title" not in details:
        t = re.search(r'<h1[^>]*><a[^>]*>([^<]+)</a>', html)
        if t:
            details["title"] = t.group(1).strip()

    if "poster" not in details or not details["poster"]:
        p = re.search(r'(?:data-src|src)="(https?://img\.fullhdfilmizlesene\.mx/poster/[^"]+\.(?:jpg|jpeg|png|webp|gif|avif))"', html)
        if p:
            details["poster"] = p.group(1)

    y = re.search(r'"datePublished":"(\d{4})', html)
    if y:
        details["year"] = y.group(1)

    yt = re.search(r'data-code="([^"]+)"', html)
    if yt:
        try:
            decoded = base64.b64decode(yt.group(1)).decode("utf-8", errors="ignore")
            yt_match = re.search(r'youtube\.com/embed/([a-zA-Z0-9_-]+)', decoded)
            if yt_match:
                details["youtube_id"] = yt_match.group(1)
                details["youtube_url"] = f"https://www.youtube.com/watch?v={yt_match.group(1)}"
        except Exception:
            pass

    yt2 = re.search(r'youtube\.com/embed/([a-zA-Z0-9_-]+)', html)
    if yt2 and "youtube_id" not in details:
        details["youtube_id"] = yt2.group(1)
        details["youtube_url"] = f"https://www.youtube.com/watch?v={yt2.group(1)}"

    vidid_match = re.search(r"var vidid = '(\d+)'", html)
    if vidid_match:
        details["vidid"] = vidid_match.group(1)

    sub_pattern = re.findall(r'(https?://[^"]+\.vtt[^"]*)', html)
    details["subtitles"] = [{"url": s, "label": "Altyazi"} for s in sub_pattern[:3]]

    return details


# =============================================================================
# SEARCH & DETAILS
# =============================================================================

async def search_all(query: str) -> list[dict]:
    results = []
    tasks = [
        search_filmmakinesi(query),
        search_fullhdfilmizlesene(query),
    ]
    for coro in asyncio.as_completed(tasks):
        try:
            r = await coro
            results.extend(r)
        except Exception:
            pass
    return results


async def get_film_details(url: str) -> dict:
    source = detect_source(url)
    if source == "filmmakinesi":
        return await get_filmmakinesi_details(url)
    if source == "fullhdfilmizlesene":
        return await get_fullhdfilmizlesene_details(url)
    return {"url": url, "source": "unknown"}


# =============================================================================
# PROBE (yt-dlp)
# =============================================================================

async def probe_video(url: str) -> dict:
    cmd = ["yt-dlp", "--no-download", "--print-json", "--no-playlist", url]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return {"error": stderr.decode(errors="ignore")[:500]}
    try:
        info = json.loads(stdout.decode())
    except json.JSONDecodeError:
        return {"error": "JSON parse hatasi"}

    formats = info.get("formats", [])
    video_formats = []
    seen_heights = set()
    for f in formats:
        h = f.get("height")
        vcodec = f.get("vcodec", "none")
        if h and vcodec != "none" and h not in seen_heights:
            seen_heights.add(h)
            video_formats.append({
                "height": h, "label": f"{h}p",
                "format_id": f.get("format_id", ""), "ext": f.get("ext", "mp4"),
            })
    video_formats.sort(key=lambda x: x["height"], reverse=True)

    audio_formats = []
    seen_audio = set()
    for f in formats:
        if f.get("acodec", "none") != "none" and f.get("vcodec", "none") == "none":
            lang = f.get("language") or f.get("format_note", "")
            abr = f.get("abr", 0)
            key = lang or f.get("format_id", "")
            if key not in seen_audio:
                seen_audio.add(key)
                audio_formats.append({
                    "language": lang or "Bilinmeyen", "format_id": f.get("format_id", ""),
                    "abr": abr, "label": f"{lang or 'Bilinmeyen'} ({abr}kbps)" if abr else lang or "Bilinmeyen",
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
    }


# =============================================================================
# VIDEO EXTRACTION
# =============================================================================

async def extract_video_from_film_page(url: str) -> dict:
    source = detect_source(url)

    if source == "filmmakinesi":
        result = await extract_hls_from_filmmakinesi(url)
        if result.get("video_url"):
            return result

    try:
        async with CurlSession(impersonate="chrome124") as session:
            resp = await session.get(url)
            if resp.status_code == 200:
                html = resp.text
                yt = re.search(r'data-code="([^"]+)"', html)
                if yt:
                    try:
                        decoded = base64.b64decode(yt.group(1)).decode("utf-8", errors="ignore")
                        yt_match = re.search(r'youtube\.com/embed/([a-zA-Z0-9_-]+)', decoded)
                        if yt_match:
                            return {"video_url": f"https://www.youtube.com/watch?v={yt_match.group(1)}", "type": "youtube"}
                    except Exception:
                        pass
                yt2 = re.search(r'youtube\.com/embed/([a-zA-Z0-9_-]+)', html)
                if yt2:
                    return {"video_url": f"https://www.youtube.com/watch?v={yt2.group(1)}", "type": "youtube"}
                m3u8 = re.findall(r'(https?://[^"\'\\s]+\.m3u8[^"\'\\s]*)', html)
                if m3u8:
                    return {"video_url": m3u8[0], "type": "hls"}
    except Exception:
        pass

    if PLAYWRIGHT_AVAILABLE:
        return await extract_video_with_playwright(url)

    return {"error": "Video URL bulunamadi"}


async def extract_video_with_playwright(page_url: str) -> dict:
    if not PLAYWRIGHT_AVAILABLE:
        return {"error": "Playwright mevcut degil"}
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
            )
            page = await context.new_page()
            video_urls = []
            def on_response(response):
                url = response.url
                ct = response.headers.get("content-type", "")
                if any(x in url for x in [".m3u8", "manifest"]):
                    video_urls.append({"url": url, "type": "hls"})
                elif ".mp4" in url and ("video" in ct or "octet" in ct or not ct):
                    video_urls.append({"url": url, "type": "mp4"})
            page.on("response", on_response)
            await page.goto(page_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(3000)
            for sel in ["#play-video", ".video-play-button", "[data-play]"]:
                try:
                    btn = page.locator(sel)
                    if await btn.count() > 0:
                        await btn.click(timeout=3000, force=True)
                        break
                except Exception:
                    continue
            await page.evaluate("document.querySelector('.ply-cover')?.remove()")
            await page.wait_for_timeout(10000)
            await browser.close()
            if video_urls:
                return {"video_url": video_urls[0]["url"], "type": video_urls[0]["type"]}
            return {"error": "Video URL bulunamadi"}
    except Exception as e:
        return {"error": f"Playwright hatasi: {str(e)[:200]}"}


# =============================================================================
# DOWNLOAD
# =============================================================================

async def download_file(url: str, output_path: Path, task_id: str) -> bool:
    try:
        async with CurlSession(impersonate="chrome124") as session:
            resp = await session.get(url, timeout=300)
            if resp.status_code != 200:
                return False
            total = len(resp.content)
            with open(output_path, "wb") as f:
                f.write(resp.content)
            if task_id in active_downloads:
                active_downloads[task_id]["progress"] = 100
            return True
    except Exception:
        return False


async def download_with_ytdlp(
    url: str, output_path: Path, task_id: str,
    format_id: str = "", audio_lang: str = "",
    sub_langs: list[str] = None, output_format: str = "mkv",
) -> tuple[bool, str]:
    cmd = ["yt-dlp", "--no-playlist", "--newline", "--progress"]
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
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    if task_id in active_downloads:
        active_downloads[task_id]["status"] = "downloading"
    async for line in proc.stdout:
        decoded = line.decode(errors="ignore").strip()
        if "[download]" in decoded and "%" in decoded:
            pct_match = re.search(r'(\d+\.?\d*)%', decoded)
            if pct_match and task_id in active_downloads:
                active_downloads[task_id]["progress"] = float(pct_match.group(1))
    _, stderr = await proc.communicate()
    return proc.returncode == 0, stderr.decode(errors="ignore")


# =============================================================================
# API ENDPOINTS
# =============================================================================

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
    try:
        async with CurlSession(impersonate="chrome124") as session:
            resp = await session.get(url)
            if resp.status_code != 200:
                return {"error": "Altyazi indirilemedi"}
            srt_content = vtt_to_srt(resp.text)
            filename = hashlib.md5(url.encode()).hexdigest()[:8] + ".srt"
            srt_path = TEMP_DIR / filename
            srt_path.write_text(srt_content, encoding="utf-8")
            return {"content": srt_content, "filename": filename}
    except Exception:
        return {"error": "Altyazi indirilemedi"}

@app.get("/api/poster")
async def api_poster(url: str = Query(...)):
    try:
        async with CurlSession(impersonate="chrome124") as session:
            resp = await session.get(url, timeout=10)
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
        "title": film_title, "status": "starting",
        "progress": 0, "save_dir": save_dir,
        "started": datetime.now().isoformat(),
    }

    async def _do_download():
        try:
            final_ext = f".{output_format}"
            final_name = f"{safe_name}{final_ext}"
            final_path = Path(save_dir) / final_name
            final_path.parent.mkdir(parents=True, exist_ok=True)

            is_youtube = "youtube.com" in download_url or "youtu.be" in download_url
            is_m3u8 = ".m3u8" in download_url

            actual_url = download_url

            if is_youtube:
                temp_out = task_dir / f"video{final_ext}"
                ok, err = await download_with_ytdlp(download_url, temp_out, task_id, format_id=format_id, audio_lang=audio_lang, sub_langs=sub_langs, output_format=output_format)
                if not ok:
                    active_downloads[task_id]["status"] = "error"
                    active_downloads[task_id]["error"] = f"yt-dlp hatasi: {err[:200]}"
                    return
                shutil.move(str(temp_out), str(final_path))

            elif is_m3u8:
                temp_out = task_dir / f"video.mp4"
                cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", download_url, "-c", "copy", "-movflags", "+faststart", str(temp_out)]
                proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                active_downloads[task_id]["status"] = "downloading"
                _, stderr = await proc.communicate()
                if proc.returncode != 0:
                    active_downloads[task_id]["status"] = "error"
                    active_downloads[task_id]["error"] = f"ffmpeg hatasi: {stderr.decode(errors='ignore')[:200]}"
                    return
                shutil.move(str(temp_out), str(final_path))

            else:
                active_downloads[task_id]["status"] = "extracting"
                active_downloads[task_id]["progress"] = 0

                result = await extract_video_from_film_page(download_url)
                if result.get("error"):
                    active_downloads[task_id]["status"] = "error"
                    active_downloads[task_id]["error"] = result["error"]
                    return

                actual_url = result.get("video_url", download_url)
                vid_type = result.get("type", "unknown")
                logger.info(f"Extracted: type={vid_type}, url={actual_url[:200]}")

                if vid_type == "hls" or ".m3u8" in actual_url:
                    temp_out = task_dir / f"video.mp4"
                    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", actual_url, "-c", "copy", "-movflags", "+faststart", str(temp_out)]
                    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    active_downloads[task_id]["status"] = "downloading"
                    _, stderr = await proc.communicate()
                    if proc.returncode != 0:
                        active_downloads[task_id]["status"] = "error"
                        active_downloads[task_id]["error"] = f"ffmpeg hatasi: {stderr.decode(errors='ignore')[:200]}"
                        return
                    shutil.move(str(temp_out), str(final_path))
                elif "youtube.com" in actual_url or "youtu.be" in actual_url:
                    temp_out = task_dir / f"video{final_ext}"
                    ok, err = await download_with_ytdlp(actual_url, temp_out, task_id, format_id=format_id, audio_lang=audio_lang, sub_langs=sub_langs, output_format=output_format)
                    if not ok:
                        active_downloads[task_id]["status"] = "error"
                        active_downloads[task_id]["error"] = f"yt-dlp hatasi: {err[:200]}"
                        return
                    shutil.move(str(temp_out), str(final_path))
                else:
                    temp_out = task_dir / f"video{final_ext}"
                    ok = await download_file(actual_url, temp_out, task_id)
                    if not ok:
                        active_downloads[task_id]["status"] = "error"
                        active_downloads[task_id]["error"] = "Video indirilemedi"
                        return
                    shutil.move(str(temp_out), str(final_path))

            if poster_url:
                poster_path = task_dir / "poster.jpg"
                await download_file(poster_url, poster_path, f"{task_id}_poster")

            active_downloads[task_id]["status"] = "completed"
            active_downloads[task_id]["path"] = str(final_path)
            active_downloads[task_id]["progress"] = 100

        except Exception as e:
            active_downloads[task_id]["status"] = "error"
            active_downloads[task_id]["error"] = str(e)

    background_tasks.add_task(_do_download)
    return {"task_id": task_id, "status": "started"}


@app.get("/api/download/{task_id}")
async def api_download_status(task_id: str):
    if task_id not in active_downloads:
        return {"error": "Gorev bulunamadi"}
    return active_downloads[task_id]

@app.get("/api/downloads")
async def api_downloads():
    return active_downloads


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8084)

"""
Ikiru Komik API — By Nat
Scrape/proxy untuk Ikiru (Nuxt SSR + /api/public/*)

Endpoint:
  GET /                              status
  GET /api/latest?page=1             update/trending
  GET /api/search?q=judul            pencarian
  GET /api/detail/{slug}             sinopsis + SEMUA chapter + sampul
  GET /api/chapter?slug=&chapter=    gambar chapter
  GET /api/chapter?url=              gambar chapter (URL penuh)
"""
from __future__ import annotations

import os
import re
import json
from typing import Any, Optional, List

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Ikiru Komik API",
    description="API komik Ikiru (ikiru.wtf) — By Nat",
    version="1.0.2",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mirror aktif dulu; angka subdomain sering ganti — override via IKIRU_MIRRORS
DEFAULT_MIRRORS = [
    "https://09.ikiru.wtf",
    "https://08.ikiru.wtf",
    "https://07.ikiru.wtf",
    "https://06.ikiru.wtf",
    "https://05.ikiru.wtf",
]
_env_mirrors = os.environ.get("IKIRU_MIRRORS", "").strip()
MIRRORS = [m.strip().rstrip("/") for m in _env_mirrors.split(",") if m.strip()] or DEFAULT_MIRRORS

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}


def _strip_html(text: str) -> str:
    t = re.sub(r"<[^>]+>", " ", text or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _chapter_num_key(val: Any) -> float:
    try:
        return float(val)
    except Exception:
        m = re.search(r"(\d+(?:\.\d+)?)", str(val or ""))
        return float(m.group(1)) if m else -1.0


async def _get_json(path: str, params: Optional[dict] = None) -> Any:
    """GET JSON dari mirror yang hidup."""
    last_err = None
    async with httpx.AsyncClient(timeout=28.0, follow_redirects=True, headers=BROWSER_HEADERS) as client:
        for base in MIRRORS:
            url = f"{base}{path}"
            try:
                r = await client.get(
                    url,
                    params=params or {},
                    headers={**BROWSER_HEADERS, "Accept": "application/json", "Referer": base + "/"},
                )
                if r.status_code == 404:
                    last_err = f"404 {base}"
                    continue
                if r.status_code != 200:
                    last_err = f"{r.status_code} {base}"
                    continue
                data = r.json()
                if isinstance(data, dict):
                    data["_mirror"] = base
                return data
            except Exception as e:
                last_err = f"{type(e).__name__}:{base}"
                continue
    raise HTTPException(502, f"Semua mirror Ikiru gagal ({last_err})")


async def _get_html(path: str) -> tuple:
    """GET HTML, return (html, base_used)."""
    last_err = None
    async with httpx.AsyncClient(timeout=35.0, follow_redirects=True, headers=BROWSER_HEADERS) as client:
        for base in MIRRORS:
            url = f"{base}{path}"
            try:
                r = await client.get(
                    url,
                    headers={
                        **BROWSER_HEADERS,
                        "Accept": "text/html,application/xhtml+xml",
                        "Referer": base + "/",
                    },
                )
                if r.status_code != 200 or len(r.text) < 1000:
                    last_err = f"{r.status_code} {base}"
                    continue
                low = r.text.lower()
                if "just a moment" in low or "cf-browser-verification" in low:
                    last_err = f"cf {base}"
                    continue
                return r.text, base
            except Exception as e:
                last_err = f"{type(e).__name__}:{base}"
                continue
    raise HTTPException(502, f"HTML Ikiru gagal ({last_err})")


def _parse_nuxt_data(html: str) -> Optional[list]:
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def _chapter_images_from_nuxt(data: list) -> List[str]:
    """Ambil URL gambar halaman chapter dari payload Nuxt.

    Format CDN Ikiru bervariasi:
      .../22/01.avif
      .../1/1-bc46a86aa984.jpg
      .../22/061.avif
    """
    if not data:
        return []
    # angka, atau angka-hash, sebelum ekstensi
    page_re = re.compile(
        r"/(\d+)(?:-[a-f0-9]+)?\.(avif|jpg|jpeg|png|webp)(\?|$)",
        re.I,
    )
    urls = []
    for x in data:
        if not isinstance(x, str):
            continue
        if "cdn.ikiru.id" not in x:
            continue
        if not page_re.search(x):
            continue
        low = x.lower()
        # skip banner / iklan / cover featured
        if any(b in low for b in ("/featured/", "/t/tester/", "sigacor", "gacor", "banner")):
            continue
        urls.append(x)

    def page_num(u: str) -> int:
        m = re.search(r"/(\d+)(?:-[a-f0-9]+)?\.(avif|jpg|jpeg|png|webp)", u, re.I)
        return int(m.group(1)) if m else 0

    seen = set()
    ordered = []
    for u in sorted(set(urls), key=page_num):
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    return ordered


def _map_manga_card(it: dict) -> dict:
    return {
        "judul": (it.get("title") or it.get("judul") or "?").strip(),
        "slug": (it.get("slug") or "").strip(),
        "thumbnail": (it.get("featuredImage") or it.get("thumbnail") or it.get("cover") or "").strip(),
        "type": (it.get("type") or "").strip(),
        "status": (it.get("status") or "").strip(),
        "id": (it.get("id") or "").strip(),
    }


def _normalize_chapters(raw: Any, mirror: str, series_slug: str) -> List[dict]:
    """List chapter lengkap + URL kanonik."""
    arr = []
    if isinstance(raw, dict):
        arr = raw.get("chapters") or raw.get("data") or []
    elif isinstance(raw, list):
        arr = raw

    out = []
    seen = set()
    for ch in arr:
        if not isinstance(ch, dict):
            continue
        num = ch.get("number")
        num_f = _chapter_num_key(num)
        title = (ch.get("title") or f"Chapter {num}").strip()
        cid = (ch.get("id") or "").strip()
        # path: chapter-22 atau chapter-5.5
        if num is not None:
            # keep decimal if present
            num_str = str(num).rstrip("0").rstrip(".") if isinstance(num, float) else str(num)
            ch_path = f"chapter-{num_str}"
        else:
            ch_path = (ch.get("slug") or cid or "chapter-0").strip()

        key = (num_f, title.lower())
        if key in seen:
            continue
        seen.add(key)

        url = f"{mirror}/manga/{series_slug}/{ch_path}/"
        out.append(
            {
                "judul": title,
                "slug": ch_path,
                "number": num,
                "id": cid,
                "url": url,
                "views": ch.get("views"),
            }
        )

    # terbaru dulu (sama seperti situs)
    out.sort(key=lambda x: _chapter_num_key(x.get("number")), reverse=True)
    return out


@app.get("/")
async def root():
    return {
        "status": "online",
        "message": "Ikiru Komik API is running",
        "version": "1.0.2",
        "author": "Nat",
        "source": "ikiru.wtf",
        "endpoints": {
            "latest": "/api/latest?page=1",
            "search": "/api/search?q=judul",
            "detail": "/api/detail/{slug}",
            "chapter": "/api/chapter?slug=series-slug&chapter=22",
            "chapter_by_url": "/api/chapter?url=https://09.ikiru.wtf/manga/.../chapter-22/",
        },
        "mirrors": MIRRORS,
    }


@app.get("/api/latest")
async def api_latest(page: int = Query(1, ge=1, le=50)):
    data = await _get_json("/api/public/manga/updated", {"page": page})
    rows = data.get("data") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        rows = []
    items = [_map_manga_card(it) for it in rows if isinstance(it, dict)]
    return {
        "status": True,
        "page": page,
        "total": len(items),
        "data": items,
        "mirror": data.get("_mirror") if isinstance(data, dict) else None,
    }


@app.get("/api/search")
async def api_search(q: str = Query(""), page: int = Query(1, ge=1)):
    q = (q or "").strip()
    if not q:
        return await api_latest(page=page)

    data = await _get_json("/api/public/manga/search", {"q": q})
    rows = data.get("data") if isinstance(data, dict) else []
    if not isinstance(rows, list):
        rows = []
    mirror = data.get("_mirror") if isinstance(data, dict) else None

    # fallback library search
    if not rows:
        lib = await _get_json("/api/public/library/search", {"q": q})
        lib_data = lib.get("data") if isinstance(lib, dict) else {}
        if isinstance(lib_data, dict):
            rows = lib_data.get("mangas") or []
        mirror = lib.get("_mirror") if isinstance(lib, dict) else mirror

    items = [_map_manga_card(it) for it in rows if isinstance(it, dict)]
    return {
        "status": True,
        "query": q,
        "page": page,
        "total": len(items),
        "data": items,
        "mirror": mirror,
    }


@app.get("/api/detail/{slug}")
async def api_detail(slug: str):
    slug = (slug or "").strip().strip("/")
    if not slug:
        raise HTTPException(400, "slug kosong")
    if "ikiru.wtf" in slug:
        m = re.search(r"/manga/([^/]+)/?", slug)
        if m:
            slug = m.group(1)

    data = await _get_json(f"/api/public/manga/{slug}")
    if not isinstance(data, dict):
        raise HTTPException(404, "Manga tidak ditemukan")

    if data.get("success") is False and data.get("data") is None:
        raise HTTPException(404, data.get("message") or "Manga tidak ditemukan")

    payload = data.get("data")
    if not isinstance(payload, dict):
        raise HTTPException(404, "Manga tidak ditemukan")

    mirror = data.get("_mirror") or MIRRORS[0]
    series_slug = (payload.get("slug") or slug).strip()
    title = (payload.get("title") or series_slug).strip()
    thumb = (payload.get("featuredImage") or "").strip()
    synopsis = _strip_html(payload.get("description") or "")
    status = (payload.get("status") or "").strip()
    mtype = (payload.get("type") or "").strip()

    chapters_raw = payload.get("chapters")
    chapters = _normalize_chapters(chapters_raw, mirror, series_slug)

    count_field = None
    has_more = False
    if isinstance(chapters_raw, dict):
        count_field = chapters_raw.get("count")
        has_more = bool(chapters_raw.get("hasMore"))

    incomplete = has_more
    if count_field is not None:
        try:
            if int(count_field) > len(chapters):
                incomplete = True
        except Exception:
            pass

    return {
        "status": True,
        "data": {
            "judul": title,
            "slug": series_slug,
            "thumbnail": thumb,
            "sinopsis": synopsis,
            "status": status,
            "type": mtype,
            "chaptersCount": len(chapters),
            "chaptersReported": count_field,
            "chaptersIncomplete": incomplete,
            "chapters": chapters,
        },
        "mirror": mirror,
    }


@app.get("/api/chapter")
async def api_chapter(
    slug: str = Query(""),
    chapter: str = Query(""),
    url: str = Query(""),
):
    """
    Ambil gambar chapter.
    - url= full chapter URL, ATAU
    - slug=series + chapter=22 / chapter-22 / 5.5
    """
    path = ""
    if url:
        u = url.strip()
        m = re.search(r"ikiru\.wtf(/manga/[^?\s#]+)", u, re.I)
        if m:
            path = m.group(1)
            if not path.endswith("/"):
                path += "/"
        elif u.startswith("/manga/"):
            path = u if u.endswith("/") else u + "/"
        else:
            raise HTTPException(400, "url chapter tidak valid")
    else:
        slug = (slug or "").strip().strip("/")
        chapter = (chapter or "").strip()
        if not slug or not chapter:
            raise HTTPException(400, "Butuh slug+chapter atau url")
        # chapter-22 | 22 | 5.5
        if chapter.lower().startswith("chapter-"):
            ch_num = chapter.split("-", 1)[-1]
        else:
            m = re.search(r"(\d+(?:\.\d+)?)", chapter)
            ch_num = m.group(1) if m else chapter
        path = f"/manga/{slug}/chapter-{ch_num}/"

    html, base = await _get_html(path)
    nuxt = _parse_nuxt_data(html)
    images = _chapter_images_from_nuxt(nuxt or [])

    if not images:
        found = re.findall(
            r"https://cdn\.ikiru\.id/[^\"\'\s]+/\d+(?:-[a-f0-9]+)?\.(?:avif|jpg|jpeg|png|webp)\?[^\"\'\s]*",
            html,
            re.I,
        )
        seen = set()
        images = []
        for u in found:
            if any(b in u.lower() for b in ("/featured/", "/t/tester/", "sigacor", "banner")):
                continue
            if u not in seen:
                seen.add(u)
                images.append(u)

    if not images:
        raise HTTPException(404, "Gambar chapter tidak ditemukan (payload kosong / chapter salah)")

    sm = re.search(r"/manga/([^/]+)/chapter-([^/]+)/?", path)
    series_slug = sm.group(1) if sm else slug
    chapter_slug = f"chapter-{sm.group(2)}" if sm else chapter

    return {
        "status": True,
        "series": series_slug,
        "chapter": chapter_slug,
        "slug": series_slug,
        "chapter_slug": chapter_slug,
        "url": f"{base}{path}",
        "total_images": len(images),
        "images": images,
        "mirror": base,
    }


# Vercel / ASGI entry
handler = app

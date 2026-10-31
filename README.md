# Ikiru Komik API v1.0.1

REST API untuk data komik dari **Ikiru** (`09.ikiru.wtf` + mirror).

**By: Nat**

## Fitur

- Pencarian judul
- Latest / trending
- Detail: **sinopsis, sampul, SEMUA chapter** (API publik Ikiru, tidak dipotong)
- Gambar chapter (parse `__NUXT_DATA__`)
- Multi-mirror otomatis

## Endpoint

| Method | Path | Keterangan |
|--------|------|------------|
| GET | `/` | Status + daftar mirror |
| GET | `/api/latest?page=1` | Update / trending |
| GET | `/api/search?q=judul` | Cari komik |
| GET | `/api/detail/{slug}` | Detail + list chapter lengkap |
| GET | `/api/chapter?slug=...&chapter=22` | Gambar chapter |
| GET | `/api/chapter?url=https://09.ikiru.wtf/manga/.../chapter-22/` | Gambar via URL |

## Contoh

```bash
curl "https://YOUR.vercel.app/api/search?q=solo"
curl "https://YOUR.vercel.app/api/detail/necromancer-foya-foya"
curl "https://YOUR.vercel.app/api/chapter?slug=necromancer-foya-foya&chapter=22"
```

## Deploy Vercel

1. Upload: `index.py`, `requirements.txt`, `vercel.json`, `README.md`, `.gitignore`
2. Vercel → New Project → Deploy
3. Opsional env:

| Key | Contoh |
|-----|--------|
| `IKIRU_MIRRORS` | `https://09.ikiru.wtf,https://08.ikiru.wtf,https://07.ikiru.wtf` |

## Catatan

- Subdomain angka Ikiru sering ganti → update `IKIRU_MIRRORS`
- Gambar chapter biasanya **AVIF** + signed URL (`cdn.ikiru.id`)
- Field `chaptersIncomplete: true` hanya jika sumber melaporkan `hasMore`

## Integrasi Nat-Multi Tools

Pakai sebagai source comic (mirip AinzScans) dengan base URL deploy API ini.

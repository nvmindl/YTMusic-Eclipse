# YouTube Music — Eclipse Music Addon

A hosted addon that adds **YouTube Music** as a source inside [Eclipse Music](https://eclipsemusic.app).
Search **tracks, albums, and artists**, browse album/artist pages natively, and stream audio — no login required.

Built with `ytmusicapi` (metadata + search) and `yt-dlp` (stream resolution).

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /manifest.json` | Addon descriptor |
| `GET /search?q=` | Tracks, albums, artists, playlists |
| `GET /stream/<id>` | Resolves a playable audio URL |
| `GET /album/<id>` | Album tracks (native browsing) |
| `GET /artist/<id>` | Artist top tracks + albums |
| `GET /playlist/<id>` | Playlist tracks |

---

## Deploy free on Render (≈3 min, no credit card)

1. Put these files in a GitHub repo (or upload the folder).
2. Go to **render.com → New → Web Service** and connect the repo.
   Render auto-detects `render.yaml`. If asked, use:
   - **Build:** `pip install -r requirements.txt`
   - **Start:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120`
   - **Plan:** Free
3. Deploy. You'll get a URL like `https://ytmusic-eclipse.onrender.com`.
4. Visit `https://<your-url>/manifest.json` to confirm it loads.

Other hosts work too — `Dockerfile` (any container host), `Procfile` (Railway/Heroku-style).

## Add it on your phone

1. Open **Eclipse → Settings → Connections → Add Connection → Addon**.
2. Paste your addon URL **including `/manifest.json`**, e.g.
   `https://ytmusic-eclipse.onrender.com/manifest.json`
3. "YouTube Music" now appears in the search source dropdown.

## Notes

- **Free Render tier sleeps** after ~15 min idle; the first request then takes ~30–60s to wake.
- `STREAM_MODE` env var:
  - `direct` (default) — returns Google's CDN URL straight to the player (fast, low bandwidth).
  - `proxy` — streams audio through your server. Switch to this **only if** playback fails on
    your phone (some CDN URLs are IP-locked to the server). Uses more host bandwidth.
- This is unofficial and not affiliated with Google/YouTube. For personal use.

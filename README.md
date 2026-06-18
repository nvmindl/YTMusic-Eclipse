# YouTube Music — Eclipse Music Addon (no-cookies edition)

Adds **YouTube Music** as a source in [Eclipse Music](https://eclipsemusic.app):
search **tracks, albums, artists** (+ playlists), browse album/artist pages, and
stream audio — **no login, no cookies**.

## How it beats YouTube's datacenter bot block

Cloud hosts (Render, Railway, Fly…) get *"Sign in to confirm you're not a bot."*
This addon bundles everything needed to get past it without cookies:

- **bgutil POT provider** (a tiny Node server) issues Proof-of-Origin Tokens so the
  `web_safari` client looks like a legitimate browser.
- **Deno** solves YouTube's JS signature challenges (yt-dlp EJS).
- Because the resolved CDN URL is **locked to the server's IP**, the addon
  **proxies** the audio through itself (`/proxy/<id>`) so it plays on your phone.
  Range/seek requests are supported and resolved URLs are cached.

All of this is baked into one Docker image — nothing to configure.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /manifest.json` | Addon descriptor |
| `GET /search?q=` | tracks / albums / artists / playlists |
| `GET /stream/<id>` | returns the proxied audio URL |
| `GET /proxy/<id>` | streams audio bytes (range-aware) |
| `GET /album/<id>` `/artist/<id>` `/playlist/<id>` | native browsing |
| `GET /health` | status |

## Deploy on Render (Docker)

> This version uses **Docker** (it needs Node + Deno + Python together), so it must
> run as a Docker service — not the old Python buildpack. If you have an existing
> Python service, create a **new** Web Service instead.

1. Put all these files in a GitHub repo (must include `Dockerfile`, `app.py`,
   `start.sh`, `requirements.txt`, `render.yaml`).
2. **render.com → New → Web Service** → connect the repo.
   Render detects `render.yaml` / the `Dockerfile` and builds the image.
   (Free plan works; the first build takes a few minutes.)
3. When live, open `https://<your-url>/health` — you should see `{"ok": true}`.

Other Docker hosts (Railway, Fly.io, Koyeb, a VPS) work the same way.

## Add it on your phone

1. Eclipse → **Settings → Connections → Add Connection → Addon**.
2. Paste your URL **including `/manifest.json`**, e.g.
   `https://ytmusic-eclipse.onrender.com/manifest.json`
3. "YouTube Music" appears in the search source dropdown.

## Notes & tuning

- **First play of a track** takes ~5–20s (resolving the stream + solving the JS
  challenge). Later tracks are faster — the player script is cached.
- **Free tier sleeps** after ~15 min idle; the first request then waits ~30–60s.
- **Memory:** the image runs Node (POT) + Deno + Python together. Render's 512 MB
  free instance is usually enough for one listener; if you see out-of-memory
  restarts, bump to a paid instance.
- **Env vars** (optional):
  - `STREAM_MODE` — `proxy` (default, works on any device) or `direct` (hand the raw
    CDN URL to Eclipse; only works if it isn't IP-locked).
  - `YT_CLIENT` — defaults to `web_safari`.
- Unofficial; not affiliated with Google/YouTube. For personal use.

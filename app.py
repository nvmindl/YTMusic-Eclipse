"""
YouTube Music addon for Eclipse Music — no-cookies edition.

Bypasses YouTube's datacenter bot check using a Proof-of-Origin Token (POT)
provider + the web_safari client, with Deno solving JS challenges. Because the
resolved CDN URL is locked to the server's IP, audio is proxied through this
server so it plays on any device (your phone).

Endpoints:
  GET /manifest.json
  GET /search?q=...        tracks / albums / artists / playlists   (ytmusicapi)
  GET /stream/<id>         returns a proxied audio URL
  GET /proxy/<id>          streams the audio bytes (range/seek aware)
  GET /album/<id>          album tracks
  GET /artist/<id>         artist top tracks + albums
  GET /playlist/<id>       playlist tracks
"""
import os
import time
import threading
import requests
import yt_dlp
from flask import Flask, request, jsonify, redirect, Response, stream_with_context
from flask_cors import CORS
from ytmusicapi import YTMusic

app = Flask(__name__)
CORS(app)
yt = YTMusic()

# direct -> hand the CDN URL to Eclipse (only works if not IP-locked; usually not)
# proxy  -> stream bytes through this server (default; works on any device)
STREAM_MODE = os.environ.get("STREAM_MODE", "proxy").lower()
PLAYER_CLIENT = os.environ.get("YT_CLIENT", "web_safari")
CACHE_DIR = os.environ.get("YTDLP_CACHE", "/tmp/ytdlp-cache")

# Resolved-URL cache so we don't re-run yt-dlp on every Range request.
_url_cache = {}            # vid -> (url, content_type, expiry_ts)
_cache_lock = threading.Lock()


def _ydl_opts():
    return {
        "quiet": True,
        "no_warnings": True,
        # 18 = progressive mp4/AAC with a real https URL; fall back to any
        # https format that carries audio.
        "format": "18/best[acodec!=none][protocol^=https]/bestaudio",
        "extractor_args": {"youtube": {"player_client": [PLAYER_CLIENT]}},
        "cachedir": CACHE_DIR,
    }


def resolve_url(video_id):
    """Return (direct_url, content_type). Cached until the URL expires."""
    now = time.time()
    with _cache_lock:
        hit = _url_cache.get(video_id)
        if hit and hit[2] > now + 60:
            return hit[0], hit[1]

    with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
        info = ydl.extract_info(
            f"https://music.youtube.com/watch?v={video_id}", download=False
        )
    url = info.get("url")
    if not url and info.get("requested_formats"):
        url = info["requested_formats"][0].get("url")
    if not url:
        raise RuntimeError("no playable url resolved")

    ext = info.get("ext", "m4a")
    ctype = "audio/mp4" if ext in ("m4a", "mp4") else f"audio/{ext}"
    # Trust the URL's own expiry if present, else cache 1h.
    expiry = now + 3600
    with _cache_lock:
        _url_cache[video_id] = (url, ctype, expiry)
    return url, ctype


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def best_thumb(thumbs):
    if not thumbs:
        return None
    return max(thumbs, key=lambda t: t.get("width", 0) or 0).get("url")


def artist_names(artists):
    if not artists:
        return ""
    return ", ".join(a.get("name", "") for a in artists if a.get("name"))


def map_track(item):
    album = item.get("album")
    return {
        "id": item.get("videoId"),
        "title": item.get("title"),
        "artist": artist_names(item.get("artists")),
        "album": album.get("name") if isinstance(album, dict) else album,
        "duration": item.get("duration_seconds"),
        "artworkURL": best_thumb(item.get("thumbnails")),
    }


# --------------------------------------------------------------------------- #
# manifest + search
# --------------------------------------------------------------------------- #
@app.route("/manifest.json")
def manifest():
    return jsonify({
        "id": "com.gumloop.ytmusic",
        "name": "YouTube Music",
        "version": "2.0.0",
        "description": "Search and stream from YouTube Music",
        "resources": ["search", "stream", "catalog"],
        "types": ["track", "album", "artist"],
        "contentType": "music",
    })


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"tracks": [], "albums": [], "artists": [], "playlists": []})

    results = yt.search(query, limit=20)
    tracks, albums, artists, playlists = [], [], [], []
    for r in results:
        t = r.get("resultType")
        if t in ("song", "video") and r.get("videoId"):
            tracks.append(map_track(r))
        elif t == "album" and r.get("browseId"):
            albums.append({
                "id": r.get("browseId"), "title": r.get("title"),
                "artist": artist_names(r.get("artists")),
                "artworkURL": best_thumb(r.get("thumbnails")), "year": r.get("year"),
            })
        elif t == "artist" and r.get("browseId"):
            artists.append({
                "id": r.get("browseId"), "name": r.get("artist"),
                "artworkURL": best_thumb(r.get("thumbnails")),
            })
        elif t == "playlist" and r.get("browseId"):
            playlists.append({
                "id": r.get("browseId"), "title": r.get("title"),
                "creator": r.get("author"),
                "artworkURL": best_thumb(r.get("thumbnails")),
                "trackCount": r.get("itemCount"),
            })
    return jsonify({"tracks": tracks, "albums": albums,
                    "artists": artists, "playlists": playlists})


# --------------------------------------------------------------------------- #
# stream + proxy
# --------------------------------------------------------------------------- #
@app.route("/stream/<video_id>")
def stream(video_id):
    try:
        url, _ = resolve_url(video_id)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 502

    if STREAM_MODE == "direct":
        return jsonify({"url": url, "format": "m4a"})

    base = request.host_url.rstrip("/")
    return jsonify({"url": f"{base}/proxy/{video_id}", "format": "m4a"})


@app.route("/proxy/<video_id>")
def proxy(video_id):
    try:
        url, ctype = resolve_url(video_id)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 502

    fwd = {}
    if request.headers.get("Range"):
        fwd["Range"] = request.headers["Range"]

    upstream = requests.get(url, headers=fwd, stream=True, timeout=30)
    out_headers = {"Accept-Ranges": "bytes", "Content-Type": ctype}
    for h in ("Content-Length", "Content-Range"):
        if h in upstream.headers:
            out_headers[h] = upstream.headers[h]

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(stream_with_context(generate()),
                    status=upstream.status_code, headers=out_headers)


# --------------------------------------------------------------------------- #
# album / artist / playlist details
# --------------------------------------------------------------------------- #
@app.route("/album/<album_id>")
def album(album_id):
    a = yt.get_album(album_id)
    tracks = [{
        "id": t.get("videoId"), "title": t.get("title"),
        "artist": artist_names(t.get("artists")) or artist_names(a.get("artists")),
        "duration": t.get("duration_seconds"),
        "artworkURL": best_thumb(t.get("thumbnails")) or best_thumb(a.get("thumbnails")),
    } for t in a.get("tracks", []) if t.get("videoId")]
    return jsonify({
        "id": album_id, "title": a.get("title"),
        "artist": artist_names(a.get("artists")),
        "artworkURL": best_thumb(a.get("thumbnails")),
        "year": a.get("year"), "description": a.get("description"),
        "trackCount": a.get("trackCount"), "tracks": tracks,
    })


@app.route("/artist/<artist_id>")
def artist(artist_id):
    ar = yt.get_artist(artist_id)
    top_tracks = [{
        "id": s.get("videoId"), "title": s.get("title"),
        "artist": artist_names(s.get("artists")) or ar.get("name"),
        "artworkURL": best_thumb(s.get("thumbnails")),
    } for s in ar.get("songs", {}).get("results", []) if s.get("videoId")]
    albums = [{
        "id": al.get("browseId"), "title": al.get("title"),
        "artist": artist_names(al.get("artists")) or ar.get("name"),
        "artworkURL": best_thumb(al.get("thumbnails")), "year": al.get("year"),
    } for al in ar.get("albums", {}).get("results", []) if al.get("browseId")]
    return jsonify({
        "id": artist_id, "name": ar.get("name"),
        "artworkURL": best_thumb(ar.get("thumbnails")),
        "bio": ar.get("description"), "topTracks": top_tracks, "albums": albums,
    })


@app.route("/playlist/<playlist_id>")
def playlist(playlist_id):
    pid = playlist_id[2:] if playlist_id.startswith("VL") else playlist_id
    p = yt.get_playlist(pid, limit=200)
    tracks = [{
        "id": t.get("videoId"), "title": t.get("title"),
        "artist": artist_names(t.get("artists")),
        "duration": t.get("duration_seconds"),
        "artworkURL": best_thumb(t.get("thumbnails")),
    } for t in p.get("tracks", []) if t.get("videoId")]
    creator = p.get("author")
    if isinstance(creator, dict):
        creator = creator.get("name")
    return jsonify({
        "id": playlist_id, "title": p.get("title"),
        "description": p.get("description"),
        "artworkURL": best_thumb(p.get("thumbnails")),
        "creator": creator, "tracks": tracks,
    })


@app.route("/health")
def health():
    return jsonify({"ok": True, "mode": STREAM_MODE, "client": PLAYER_CLIENT})


@app.route("/")
def home():
    return redirect("/manifest.json")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 3000)))

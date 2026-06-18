"""
YouTube Music addon for Eclipse Music.

Exposes the endpoints Eclipse expects:
  GET /manifest.json      - describes the addon
  GET /search?q=...       - tracks / albums / artists / playlists
  GET /stream/<id>        - resolves a playable audio URL (via yt-dlp)
  GET /album/<id>         - album tracks (native browsing)
  GET /artist/<id>        - artist top tracks + albums
  GET /playlist/<id>      - playlist tracks

No login required. Powered by ytmusicapi (metadata) + yt-dlp (stream URLs).
"""
import os
import yt_dlp
from flask import Flask, request, jsonify, redirect, Response, stream_with_context
from flask_cors import CORS
from ytmusicapi import YTMusic

app = Flask(__name__)
CORS(app)

yt = YTMusic()

# direct  -> return Google's CDN URL straight to Eclipse (fast, low bandwidth)
# proxy   -> stream the audio through this server (works even if the CDN URL is
#            IP-locked to the server, but uses the host's bandwidth)
STREAM_MODE = os.environ.get("STREAM_MODE", "direct").lower()

# yt-dlp clients that still return real audio URLs after YouTube's SABR change.
# 'android' yields m4a/AAC (best Eclipse compatibility); music clients yield opus.
YDL_CLIENTS = ["android", "android_music", "ios_music"]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def best_thumb(thumbnails):
    """Return the highest-resolution thumbnail URL from a ytmusicapi list."""
    if not thumbnails:
        return None
    return max(thumbnails, key=lambda t: t.get("width", 0) or 0).get("url")


def artist_names(artists):
    """Join a ytmusicapi artists list into a display string."""
    if not artists:
        return ""
    return ", ".join(a.get("name", "") for a in artists if a.get("name"))


def map_track(item):
    """Map a ytmusicapi song/track dict to an Eclipse track object."""
    album = item.get("album")
    return {
        "id": item.get("videoId"),
        "title": item.get("title"),
        "artist": artist_names(item.get("artists")),
        "album": album.get("name") if isinstance(album, dict) else album,
        "duration": item.get("duration_seconds"),
        "artworkURL": best_thumb(item.get("thumbnails")),
    }


def extract_stream(video_id):
    """Resolve a direct audio URL for a YouTube video id using yt-dlp."""
    last_err = None
    for client in YDL_CLIENTS:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "extractor_args": {"youtube": {"player_client": [client]}},
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(
                    f"https://music.youtube.com/watch?v={video_id}", download=False
                )
            url = info.get("url")
            if url:
                ext = info.get("ext", "m4a")
                fmt = "m4a" if ext in ("m4a", "mp4") else ("ogg" if ext == "webm" else ext)
                abr = info.get("abr")
                return {
                    "url": url,
                    "format": fmt,
                    "quality": f"{int(abr)}kbps" if abr else None,
                }
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            continue
    raise RuntimeError(last_err or "no playable format found")


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #
@app.route("/manifest.json")
def manifest():
    return jsonify({
        "id": "com.gumloop.ytmusic",
        "name": "YouTube Music",
        "version": "1.0.0",
        "description": "Search and stream from YouTube Music",
        "resources": ["search", "stream", "catalog"],
        "types": ["track", "album", "artist"],
        "contentType": "music",
    })


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"tracks": [], "albums": [], "artists": [], "playlists": []})

    results = yt.search(query, limit=20)
    tracks, albums, artists, playlists = [], [], [], []

    for r in results:
        rtype = r.get("resultType")
        if rtype in ("song", "video") and r.get("videoId"):
            tracks.append(map_track(r))
        elif rtype == "album" and r.get("browseId"):
            albums.append({
                "id": r.get("browseId"),
                "title": r.get("title"),
                "artist": artist_names(r.get("artists")),
                "artworkURL": best_thumb(r.get("thumbnails")),
                "year": r.get("year"),
            })
        elif rtype == "artist" and r.get("browseId"):
            artists.append({
                "id": r.get("browseId"),
                "name": r.get("artist"),
                "artworkURL": best_thumb(r.get("thumbnails")),
            })
        elif rtype == "playlist" and r.get("browseId"):
            playlists.append({
                "id": r.get("browseId"),
                "title": r.get("title"),
                "creator": r.get("author"),
                "artworkURL": best_thumb(r.get("thumbnails")),
                "trackCount": r.get("itemCount"),
            })

    return jsonify({
        "tracks": tracks,
        "albums": albums,
        "artists": artists,
        "playlists": playlists,
    })


# --------------------------------------------------------------------------- #
# stream
# --------------------------------------------------------------------------- #
@app.route("/stream/<video_id>")
def stream(video_id):
    try:
        resolved = extract_stream(video_id)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 502

    if STREAM_MODE == "proxy":
        base = request.host_url.rstrip("/")
        return jsonify({"url": f"{base}/proxy/{video_id}", "format": resolved["format"]})

    return jsonify(resolved)


@app.route("/proxy/<video_id>")
def proxy(video_id):
    """Stream the audio bytes through this server (for IP-locked CDN URLs)."""
    import requests as _rq
    try:
        resolved = extract_stream(video_id)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 502

    upstream = _rq.get(resolved["url"], stream=True, timeout=30)
    ctype = upstream.headers.get("Content-Type", "audio/mp4")

    def generate():
        for chunk in upstream.iter_content(chunk_size=64 * 1024):
            if chunk:
                yield chunk

    return Response(stream_with_context(generate()), content_type=ctype)


# --------------------------------------------------------------------------- #
# album / artist / playlist details
# --------------------------------------------------------------------------- #
@app.route("/album/<album_id>")
def album(album_id):
    a = yt.get_album(album_id)
    tracks = [{
        "id": t.get("videoId"),
        "title": t.get("title"),
        "artist": artist_names(t.get("artists")) or artist_names(a.get("artists")),
        "duration": t.get("duration_seconds"),
        "artworkURL": best_thumb(t.get("thumbnails")) or best_thumb(a.get("thumbnails")),
    } for t in a.get("tracks", []) if t.get("videoId")]

    return jsonify({
        "id": album_id,
        "title": a.get("title"),
        "artist": artist_names(a.get("artists")),
        "artworkURL": best_thumb(a.get("thumbnails")),
        "year": a.get("year"),
        "description": a.get("description"),
        "trackCount": a.get("trackCount"),
        "tracks": tracks,
    })


@app.route("/artist/<artist_id>")
def artist(artist_id):
    ar = yt.get_artist(artist_id)
    art = best_thumb(ar.get("thumbnails"))

    top_tracks = [{
        "id": s.get("videoId"),
        "title": s.get("title"),
        "artist": artist_names(s.get("artists")) or ar.get("name"),
        "artworkURL": best_thumb(s.get("thumbnails")),
    } for s in ar.get("songs", {}).get("results", []) if s.get("videoId")]

    albums = [{
        "id": al.get("browseId"),
        "title": al.get("title"),
        "artist": artist_names(al.get("artists")) or ar.get("name"),
        "artworkURL": best_thumb(al.get("thumbnails")),
        "year": al.get("year"),
    } for al in ar.get("albums", {}).get("results", []) if al.get("browseId")]

    return jsonify({
        "id": artist_id,
        "name": ar.get("name"),
        "artworkURL": art,
        "bio": ar.get("description"),
        "topTracks": top_tracks,
        "albums": albums,
    })


@app.route("/playlist/<playlist_id>")
def playlist(playlist_id):
    pid = playlist_id[2:] if playlist_id.startswith("VL") else playlist_id
    p = yt.get_playlist(pid, limit=200)
    tracks = [{
        "id": t.get("videoId"),
        "title": t.get("title"),
        "artist": artist_names(t.get("artists")),
        "duration": t.get("duration_seconds"),
        "artworkURL": best_thumb(t.get("thumbnails")),
    } for t in p.get("tracks", []) if t.get("videoId")]

    return jsonify({
        "id": playlist_id,
        "title": p.get("title"),
        "description": p.get("description"),
        "artworkURL": best_thumb(p.get("thumbnails")),
        "creator": (p.get("author") or {}).get("name") if isinstance(p.get("author"), dict) else p.get("author"),
        "tracks": tracks,
    })


@app.route("/")
def home():
    return redirect("/manifest.json")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)

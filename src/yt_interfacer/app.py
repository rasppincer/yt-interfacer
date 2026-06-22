"""Flask application — YouTube API endpoints.

Endpoints:
    GET  /health                   — Health check
    GET  /api/transcript?url=...   — Fetch YouTube transcript
    POST /api/archive              — Download video as MP3
    GET  /api/archives             — List archived MP3s
    GET  /api/archives/<filename>  — Stream/download an MP3
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, jsonify, request, send_file

from .archiver import archive_as_mp3, archive_playlist, list_archives, list_playlist_videos
from .channel import archive_channel_transcripts, list_archived_transcripts, list_channel_videos
from .transcript import get_transcript
from .cache import search_transcripts, list_cached_metadata, get_cached_metadata

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

ARCHIVE_DIR = Path(__file__).resolve().parents[2] / "archive"


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.route("/health")
def health():
    """Health check."""
    archives = list_archives(ARCHIVE_DIR)
    return jsonify({
        "status": "ok",
        "archived_count": len(archives),
    })


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------


@app.route("/api/transcript")
def transcript_endpoint():
    """Fetch YouTube transcript.

    Query params:
        url (required): YouTube URL or video ID.
        lang: Language code (default "en").
        timestamps: "true" to include timestamps (default "true").
    """
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Missing 'url' parameter"}), 400

    lang = request.args.get("lang", "en")
    include_ts = request.args.get("timestamps", "true").lower() == "true"

    try:
        result = get_transcript(url, lang=lang, include_timestamps=include_ts)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 404

    return jsonify(result)


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


@app.route("/api/archive", methods=["POST"])
def archive_endpoint():
    """Download YouTube video as MP3.

    JSON body:
        url (required): YouTube URL or video ID.
        title (optional): Custom filename.
    """
    data = request.get_json(silent=True) or {}
    url = data.get("url")
    if not url:
        return jsonify({"error": "Missing 'url' in body"}), 400

    title = data.get("title")

    try:
        result = archive_as_mp3(url, title=title, archive_dir=ARCHIVE_DIR)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "filename": result.filename,
        "path": result.path,
        "title": result.title,
        "duration_seconds": result.duration_seconds,
        "size_mb": result.size_mb,
        "video_id": result.video_id,
    })


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Playlist
# ---------------------------------------------------------------------------


@app.route("/api/playlist")
def playlist_list_endpoint():
    """List videos in a YouTube playlist.

    Query params:
        url (required): YouTube playlist URL.
    """
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Missing 'url' parameter"}), 400

    try:
        videos = list_playlist_videos(url)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"count": len(videos), "videos": videos})


@app.route("/api/playlist/archive", methods=["POST"])
def playlist_archive_endpoint():
    """Download all videos in a playlist as MP3.

    JSON body:
        url (required): YouTube playlist URL.
    """
    data = request.get_json(silent=True) or {}
    url = data.get("url")
    if not url:
        return jsonify({"error": "Missing 'url' in body"}), 400

    try:
        results = archive_playlist(url, archive_dir=ARCHIVE_DIR)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "total": len(results),
        "archived": [
            {
                "filename": r.filename,
                "title": r.title,
                "size_mb": r.size_mb,
                "video_id": r.video_id,
            }
            for r in results
        ],
    })


# ---------------------------------------------------------------------------
# List / Serve archives
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Channel transcripts
# ---------------------------------------------------------------------------


@app.route("/api/channel")
def channel_list_endpoint():
    """List videos in a YouTube channel.

    Query params:
        url (required): YouTube channel URL.
    """
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Missing 'url' parameter"}), 400

    try:
        videos = list_channel_videos(url)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"count": len(videos), "videos": videos})


@app.route("/api/channel/archive", methods=["POST"])
def channel_archive_endpoint():
    """Archive transcripts for all videos in a channel.

    JSON body:
        url (required): YouTube channel URL.
        lang: Language code (default "en").
    """
    data = request.get_json(silent=True) or {}
    url = data.get("url")
    if not url:
        return jsonify({"error": "Missing 'url' in body"}), 400

    lang = data.get("lang", "en")

    try:
        results = archive_channel_transcripts(url, lang=lang)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "total": len(results),
        "archived": [
            {
                "video_id": r.video_id,
                "title": r.title,
                "language": r.language,
                "path": r.path,
                "segment_count": r.segment_count,
            }
            for r in results
        ],
    })


@app.route("/api/channel/transcripts")
def channel_transcripts_endpoint():
    """List archived transcript files.

    Query params:
        channel: Filter by channel name (optional).
    """
    channel = request.args.get("channel")
    entries = list_archived_transcripts(channel=channel)
    return jsonify({"count": len(entries), "transcripts": entries})



@app.route("/api/archives")
def archives_list_endpoint():
    """List all archived MP3s."""
    return jsonify({"archives": list_archives(ARCHIVE_DIR)})


@app.route("/api/archives/<path:filename>")
def archives_serve_endpoint(filename: str):
    """Stream/download an archived MP3."""
    filepath = ARCHIVE_DIR / filename
    if not filepath.exists() or not filepath.suffix == ".mp3":
        return jsonify({"error": "File not found"}), 404
    return send_file(filepath, mimetype="audio/mpeg", as_attachment=True)


# ---------------------------------------------------------------------------
# Search & Metadata Cache
# ---------------------------------------------------------------------------


@app.route("/api/search")
def search_endpoint():
    """Search cached transcripts.

    Query params:
        q (required): Search string.
        limit: Max results (default 50).
    """
    q = request.args.get("q")
    if not q:
        return jsonify({"error": "Missing 'q' parameter"}), 400

    limit = request.args.get("limit", 50, type=int)
    results = search_transcripts(q, limit=limit)
    return jsonify({"query": q, "count": len(results), "results": results})


@app.route("/api/metadata")
def metadata_list_endpoint():
    """List all cached video metadata."""
    entries = list_cached_metadata()
    return jsonify({"count": len(entries), "metadata": entries})


@app.route("/api/metadata/<video_id>")
def metadata_get_endpoint(video_id: str):
    """Get cached metadata for a single video."""
    meta = get_cached_metadata(video_id)
    if not meta:
        return jsonify({"error": f"No cached metadata for {video_id}"}), 404
    return jsonify(meta)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main():
    """Run the server."""
    app.run(host="0.0.0.0", port=8323, debug=False)


if __name__ == "__main__":
    main()

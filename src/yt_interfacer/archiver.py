"""MP3 archival via yt-dlp.

Downloads YouTube audio and converts to MP3 using ffmpeg.
Maintains a local archive directory.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_ARCHIVE_DIR = Path(__file__).resolve().parents[2] / "archive"
from .cache import cache_metadata, get_cached_metadata



def _yt_dlp_path() -> str:
    """Find yt-dlp binary — prefer venv-local, fall back to PATH."""
    venv_bin = Path(sys.executable).parent / "yt-dlp"
    if venv_bin.exists():
        return str(venv_bin)
    return "yt-dlp"


@dataclass
class ArchiveResult:
    """Result of an archive operation."""

    filename: str
    path: str
    title: str
    duration_seconds: float | None
    size_mb: float
    video_id: str


def _sanitize_filename(name: str) -> str:
    """Remove unsafe characters from filename."""
    name = re.sub(r"[<>:\"/'\\|?*\[\]@]", "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name[:200]  # cap length


def get_metadata(url: str) -> dict:
    """Fetch video metadata without downloading.

    Args:
        url: YouTube URL or video ID.

    Returns:
        Dict with title, duration, uploader, etc.
    """
    # Extract video ID for cache lookup
    video_id = None
    id_match = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
    if id_match:
        video_id = id_match.group(1)
    elif re.fullmatch(r'[a-zA-Z0-9_-]{11}', url):
        video_id = url

    if video_id:
        cached = get_cached_metadata(video_id)
        if cached:
            logger.info("Metadata cache hit: %s", video_id)
            return cached

    cmd = [
        _yt_dlp_path(),
        "--dump-json",
        "--no-download",
        "--no-playlist",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp metadata failed: {result.stderr.strip()}")
    meta = json.loads(result.stdout)
    vid = meta.get("id", url)
    try:
        cache_metadata(vid, meta)
    except Exception:
        logger.warning("Failed to cache metadata for %s", vid, exc_info=True)
    return meta


def archive_as_mp3(
    url: str,
    title: str | None = None,
    archive_dir: Path | str | None = None,
) -> ArchiveResult:
    """Download YouTube audio as MP3.

    Args:
        url: YouTube URL or video ID.
        title: Custom filename (without extension). If None, uses video title.
        archive_dir: Directory to save MP3. Defaults to ./archive/.

    Returns:
        ArchiveResult with filename, path, title, duration, size.

    Raises:
        RuntimeError: If download fails.
    """
    archive_path = Path(archive_dir) if archive_dir else DEFAULT_ARCHIVE_DIR
    archive_path.mkdir(parents=True, exist_ok=True)

    # Fetch metadata first to get the title and video ID
    meta = get_metadata(url)
    video_id = meta.get("id", "unknown")
    video_title = title or meta.get("title", video_id)
    duration = meta.get("duration")
    safe_name = _sanitize_filename(video_title)
    output_file = archive_path / f"{safe_name}.mp3"

    # Skip if already archived
    if output_file.exists():
        size_mb = output_file.stat().st_size / (1024 * 1024)
        logger.info("Already archived: %s", output_file.name)
        return ArchiveResult(
            filename=output_file.name,
            path=str(output_file),
            title=video_title,
            duration_seconds=duration,
            size_mb=round(size_mb, 2),
            video_id=video_id,
        )

    # Download best audio → MP3
    cmd = [
        _yt_dlp_path(),
        "-x",                          # extract audio
        "--audio-format", "mp3",
        "--audio-quality", "0",        # best quality
        "--no-playlist",               # single video only
        "-o", str(output_file),
        url,
    ]

    logger.info("Downloading: %s → %s", video_id, output_file.name)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp download failed: {result.stderr.strip()}")

    if not output_file.exists():
        # yt-dlp might add extension differently
        candidates = list(archive_path.glob(f"{safe_name}*"))
        if candidates:
            output_file = candidates[0]
        else:
            raise RuntimeError(f"Download completed but file not found at {output_file}")

    size_mb = output_file.stat().st_size / (1024 * 1024)

    return ArchiveResult(
        filename=output_file.name,
        path=str(output_file),
        title=video_title,
        duration_seconds=duration,
        size_mb=round(size_mb, 2),
        video_id=video_id,
    )


def list_playlist_videos(url: str) -> list[dict]:
    """List all videos in a YouTube playlist (metadata only, no download).

    Args:
        url: YouTube playlist URL.

    Returns:
        List of dicts with id, title, duration, url.
    """
    cmd = [
        _yt_dlp_path(),
        "--dump-json",
        "--no-download",
        "--flat-playlist",
        url,
    ]
    logger.info("Listing playlist: %s", url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp playlist listing failed: {result.stderr.strip()}")

    videos = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        entry = json.loads(line)
        videos.append({
            "id": entry.get("id", ""),
            "title": entry.get("title", "Unknown"),
            "duration": entry.get("duration"),
            "url": entry.get("url") or entry.get("webpage_url") or f"https://www.youtube.com/watch?v={entry.get('id', '')}",
        })
    return videos


def archive_playlist(
    url: str,
    archive_dir: Path | str | None = None,
) -> list[ArchiveResult]:
    """Download all videos in a playlist as MP3.

    Args:
        url: YouTube playlist URL.
        archive_dir: Directory to save MP3s. Defaults to ./archive/.

    Returns:
        List of ArchiveResult for each video (skipped if already exists).
    """
    videos = list_playlist_videos(url)
    logger.info("Playlist has %d videos", len(videos))

    results = []
    for i, video in enumerate(videos):
        video_url = video["url"]
        logger.info("Archiving [%d/%d]: %s", i + 1, len(videos), video["title"])
        try:
            result = archive_as_mp3(video_url, archive_dir=archive_dir)
            results.append(result)
        except Exception as e:
            logger.error("Failed to archive %s: %s", video["title"], e)
            # Continue with next video instead of failing the whole playlist
            continue
    return results


def list_archives(archive_dir: Path | str | None = None) -> list[dict]:
    """List all archived MP3 files.

    Returns:
        List of dicts with filename, size_mb, modified date.
    """
    archive_path = Path(archive_dir) if archive_dir else DEFAULT_ARCHIVE_DIR
    if not archive_path.exists():
        return []

    files = []
    for mp3 in sorted(archive_path.glob("*.mp3"), key=lambda f: f.stat().st_mtime, reverse=True):
        stat = mp3.stat()
        files.append({
            "filename": mp3.name,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return files

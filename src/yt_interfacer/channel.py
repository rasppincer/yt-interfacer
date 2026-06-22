"""Channel transcript archival.

Lists videos in a YouTube channel and archives their transcripts as text files.
Mirrors the playlist pattern from archiver.py but saves .txt instead of .mp3.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .transcript import get_transcript

logger = logging.getLogger(__name__)

DEFAULT_TRANSCRIPT_DIR = Path(__file__).resolve().parents[2] / "transcripts"


def _yt_dlp_path() -> str:
    """Find yt-dlp binary — prefer venv-local, fall back to PATH."""
    venv_bin = Path(sys.executable).parent / "yt-dlp"
    if venv_bin.exists():
        return str(venv_bin)
    return "yt-dlp"


def _sanitize_filename(name: str) -> str:
    """Remove unsafe characters from filename."""
    name = re.sub(r"[<>:\"/'\\|?*\[\]@]", "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name[:200]


@dataclass
class TranscriptArchiveResult:
    """Result of archiving one video's transcript."""

    video_id: str
    title: str
    language: str
    path: str
    segment_count: int


def list_channel_videos(url: str) -> list[dict]:
    """List all videos in a YouTube channel.

    Args:
        url: YouTube channel URL (any format — /@handle, /channel/ID, /c/name).

    Returns:
        List of dicts with id, title, duration, url.
    """
    # Ensure we're hitting the /videos tab
    if not url.endswith("/videos"):
        url = url.rstrip("/") + "/videos"

    cmd = [
        _yt_dlp_path(),
        "--dump-json",
        "--no-download",
        "--flat-playlist",
        "--playlist-items", "1-500",  # cap at 500 videos
        url,
    ]
    logger.info("Listing channel: %s", url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp channel listing failed: {result.stderr.strip()}")

    videos = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        entry = _parse_jsonl(line)
        if not entry:
            continue
        videos.append({
            "id": entry.get("id", ""),
            "title": entry.get("title", "Unknown"),
            "duration": entry.get("duration"),
            "url": entry.get("url") or entry.get("webpage_url")
                    or f"https://www.youtube.com/watch?v={entry.get('id', '')}",
        })
    return videos


def _parse_jsonl(line: str) -> dict | None:
    """Parse a JSON line, returning None on failure."""
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        logger.warning("Skipping unparseable JSON line")
        return None


def _fetch_upload_date(video_id: str) -> tuple[str, str]:
    """Fetch upload date for a single video. Returns (video_id, date_str)."""
    try:
        cmd = [
            _yt_dlp_path(),
            "--print", "upload_date",
            "--no-download",
            "--no-playlist",
            f"https://www.youtube.com/watch?v={video_id}",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        date = r.stdout.strip()
        if date and re.fullmatch(r"\d{8}", date):
            return video_id, date
    except Exception:
        pass
    return video_id, ""


def _batch_fetch_upload_dates(video_ids: list[str], workers: int = 8) -> dict[str, str]:
    """Fetch upload dates for many videos in parallel. Returns {video_id: date}."""
    dates: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_fetch_upload_date, vid): vid for vid in video_ids}
        for f in as_completed(futs):
            vid, date = f.result()
            if date:
                dates[vid] = date
    return dates


def _find_existing_transcript(channel_dir: Path, video_id: str) -> Path | None:
    """Check if a transcript already exists for this video (any filename pattern)."""
    for existing in channel_dir.glob(f"*__{video_id}.txt"):
        return existing
    return None


def archive_channel_transcripts(
    url: str,
    lang: str = "en",
    output_dir: Path | str | None = None,
) -> list[TranscriptArchiveResult]:
    """Fetch and save transcripts for all videos in a channel.

    Skips videos that already have a saved transcript file or that have
    no available transcript. Filenames are prefixed with upload date
    (YYYYMMDD) for natural chronological sorting.

    Args:
        url: YouTube channel URL.
        lang: Preferred language code (default "en").
        output_dir: Directory for .txt files. Defaults to ./transcripts/<channel>/.

    Returns:
        List of TranscriptArchiveResult for each successfully archived video.
    """
    videos = list_channel_videos(url)
    logger.info("Channel has %d videos", len(videos))

    if not videos:
        return []

    # Batch-fetch upload dates (parallel)
    video_ids = [v["id"] for v in videos]
    logger.info("Fetching upload dates for %d videos...", len(video_ids))
    upload_dates = _batch_fetch_upload_dates(video_ids)
    logger.info("Got upload dates for %d/%d videos", len(upload_dates), len(video_ids))

    # Use channel handle or ID as subdirectory name
    channel_name = _extract_channel_dir_name(url)
    base_dir = Path(output_dir) if output_dir else DEFAULT_TRANSCRIPT_DIR
    channel_dir = base_dir / channel_name
    channel_dir.mkdir(parents=True, exist_ok=True)

    results: list[TranscriptArchiveResult] = []
    skipped = 0
    failed = 0

    for i, video in enumerate(videos):
        video_id = video["id"]
        title = video["title"]
        safe_name = _sanitize_filename(title)
        date_prefix = upload_dates.get(video_id, "")
        if date_prefix:
            filename = f"{date_prefix}_{safe_name}__{video_id}.txt"
        else:
            filename = f"{safe_name}__{video_id}.txt"
        output_file = channel_dir / filename

        # Skip if already archived (check any filename pattern with this video_id)
        existing = _find_existing_transcript(channel_dir, video_id)
        if existing:
            logger.info("[%d/%d] Already archived: %s", i + 1, len(videos), title)
            skipped += 1
            continue

        logger.info("[%d/%d] Fetching transcript: %s", i + 1, len(videos), title)
        try:
            result = get_transcript(video_id, lang=lang, include_timestamps=False)
        except (RuntimeError, ValueError) as e:
            logger.warning("No transcript for %s: %s", title, e)
            failed += 1
            continue

        full_text = result.get("full_text", "")
        if not full_text.strip():
            logger.warning("Empty transcript for %s, skipping", title)
            failed += 1
            continue

        # Write transcript file
        header = f"# {title}\n# https://www.youtube.com/watch?v={video_id}\n\n"
        output_file.write_text(header + full_text, encoding="utf-8")

        results.append(TranscriptArchiveResult(
            video_id=video_id,
            title=title,
            language=result.get("language", lang),
            path=str(output_file),
            segment_count=len(result.get("segments", [])),
        ))

    logger.info(
        "Channel archive done: %d saved, %d skipped (existing), %d failed (no transcript)",
        len(results), skipped, failed,
    )
    return results


def _extract_channel_dir_name(url: str) -> str:
    """Extract a filesystem-safe directory name from a channel URL.

    Examples:
        https://www.youtube.com/@3Blue1Brown  →  3Blue1Brown
        https://www.youtube.com/channel/UC...  →  UC...
        https://www.youtube.com/c/SomeChannel →  SomeChannel
    """
    # @handle
    m = re.search(r"youtube\.com/@([a-zA-Z0-9_.-]+)", url)
    if m:
        return _sanitize_filename(m.group(1))

    # /channel/ID
    m = re.search(r"youtube\.com/channel/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)

    # /c/Name
    m = re.search(r"youtube\.com/c/([a-zA-Z0-9_.-]+)", url)
    if m:
        return _sanitize_filename(m.group(1))

    # Fallback: use last URL segment
    return _sanitize_filename(url.rstrip("/").split("/")[-1] or "unknown_channel")


def list_archived_transcripts(
    channel: str | None = None,
    output_dir: Path | str | None = None,
) -> list[dict]:
    """List archived transcript files.

    Args:
        channel: If set, list only this channel's transcripts.
        output_dir: Base transcripts directory. Defaults to ./transcripts/.

    Returns:
        List of dicts with filename, channel, video_id, path.
    """
    base_dir = Path(output_dir) if output_dir else DEFAULT_TRANSCRIPT_DIR
    if not base_dir.exists():
        return []

    files: list[dict] = []
    if channel:
        channel_dir = base_dir / channel
        if channel_dir.exists():
            files.extend(_scan_channel_dir(channel_dir, channel))
    else:
        for ch_dir in sorted(base_dir.iterdir()):
            if ch_dir.is_dir():
                files.extend(_scan_channel_dir(ch_dir, ch_dir.name))
    return files


def _scan_channel_dir(channel_dir: Path, channel_name: str) -> list[dict]:
    """Scan a channel directory for .txt transcript files."""
    entries = []
    for txt_file in sorted(channel_dir.glob("*.txt")):
        # Extract video_id from filename pattern: [YYYYMMDD_]Title__VIDEOID.txt
        stem = txt_file.stem
        video_id = stem.split("__")[-1] if "__" in stem else ""
        entries.append({
            "filename": txt_file.name,
            "channel": channel_name,
            "video_id": video_id,
            "size_kb": round(txt_file.stat().st_size / 1024, 1),
            "path": str(txt_file),
        })
    return entries

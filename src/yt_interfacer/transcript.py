"""YouTube transcript fetcher.

Wraps youtube-transcript-api to extract transcripts with timestamps.
"""

from __future__ import annotations

import logging
import re

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter
from .cache import cache_transcript, get_cached_transcript

logger = logging.getLogger(__name__)

# Matches youtube.com/watch?v=XXX, youtu.be/XXX, youtube.com/embed/XXX
_YT_URL_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})"
)


def extract_video_id(url: str) -> str | None:
    """Extract 11-char video ID from a YouTube URL.

    Args:
        url: Full YouTube URL or bare video ID.

    Returns:
        Video ID string, or None if not recognized.
    """
    # Bare video ID
    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", url):
        return url

    m = _YT_URL_RE.search(url)
    return m.group(1) if m else None


def get_transcript(
    url: str,
    lang: str = "en",
    include_timestamps: bool = True,
) -> dict:
    """Fetch transcript for a YouTube video.

    Args:
        url: YouTube URL or video ID.
        lang: Preferred language code (default "en"). Falls back to any available.
        include_timestamps: If True, return segments with start/duration.

    Returns:
        Dict with keys: video_id, language, segments (list of dicts),
        full_text (joined string).

    Raises:
        ValueError: If URL is invalid.
        RuntimeError: If transcript cannot be fetched.
    """
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError(f"Invalid YouTube URL: {url}")

    cached = get_cached_transcript(video_id, lang)
    if cached:
        logger.info("Cache hit for transcript: %s (%s)", video_id, lang)
        return cached

    try:
        ytt = YouTubeTranscriptApi()
        transcript = ytt.fetch(video_id, languages=[lang])
    except Exception:
        # Try without language preference
        try:
            transcript = ytt.fetch(video_id)
        except Exception as e:
            raise RuntimeError(f"No transcript available for {video_id}: {e}") from e

    segments = []
    texts = []
    for snippet in transcript:
        text = snippet.text
        texts.append(text)
        if include_timestamps:
            segments.append({
                "start": round(snippet.start, 2),
                "duration": round(snippet.duration, 2),
                "text": text,
            })

    result = {
        "video_id": video_id,
        "language": getattr(transcript, "language", lang),
        "segments": segments if include_timestamps else [],
        "full_text": " ".join(texts),
    }
    try:
        cache_transcript(video_id, result["language"], result["segments"], result["full_text"])
    except Exception:
        logger.warning("Failed to cache transcript for %s", video_id, exc_info=True)
    return result

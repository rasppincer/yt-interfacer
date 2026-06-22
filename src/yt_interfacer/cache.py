"""SQLite cache for video metadata and transcripts.

Stores metadata (title, duration, channel) and transcripts so we don't
re-fetch from YouTube on every request. Cache lives at data/cache.db.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "cache.db"


def _get_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Get a SQLite connection, creating tables if needed."""
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS video_metadata (
            video_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            duration REAL,
            channel TEXT,
            uploader TEXT,
            upload_date TEXT,
            thumbnail TEXT,
            description TEXT,
            fetched_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transcripts (
            video_id TEXT NOT NULL,
            language TEXT NOT NULL,
            segments_json TEXT NOT NULL,
            full_text TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (video_id, language)
        );
        CREATE INDEX IF NOT EXISTS idx_transcripts_text ON transcripts(full_text);
    """)
    conn.commit()
    return conn


# ── Metadata ────────────────────────────────────────────────────────────

def cache_metadata(video_id: str, meta: dict[str, Any]) -> None:
    """Store video metadata in the cache."""
    conn = _get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT OR REPLACE INTO video_metadata
               (video_id, title, duration, channel, uploader, upload_date,
                thumbnail, description, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                video_id,
                meta.get("title", ""),
                meta.get("duration"),
                meta.get("channel") or meta.get("uploader", ""),
                meta.get("uploader", ""),
                meta.get("upload_date", ""),
                meta.get("thumbnail", ""),
                (meta.get("description") or "")[:2000],
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_cached_metadata(video_id: str) -> dict[str, Any] | None:
    """Retrieve cached metadata for a video. Returns None if not cached."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM video_metadata WHERE video_id = ?", (video_id,)
        ).fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        conn.close()


def list_cached_metadata() -> list[dict[str, Any]]:
    """List all cached metadata entries."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM video_metadata ORDER BY title"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Transcripts ─────────────────────────────────────────────────────────

def cache_transcript(video_id: str, language: str, segments: list[dict], full_text: str) -> None:
    """Store a transcript in the cache."""
    conn = _get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT OR REPLACE INTO transcripts
               (video_id, language, segments_json, full_text, fetched_at)
               VALUES (?, ?, ?, ?, ?)""",
            (video_id, language, json.dumps(segments), full_text, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_cached_transcript(video_id: str, language: str = "en") -> dict | None:
    """Retrieve a cached transcript. Returns None if not cached."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM transcripts WHERE video_id = ? AND language = ?",
            (video_id, language),
        ).fetchone()
        if not row:
            # Try any language
            row = conn.execute(
                "SELECT * FROM transcripts WHERE video_id = ?", (video_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "video_id": row["video_id"],
            "language": row["language"],
            "segments": json.loads(row["segments_json"]),
            "full_text": row["full_text"],
        }
    finally:
        conn.close()


def search_transcripts(query: str, limit: int = 50) -> list[dict[str, Any]]:
    """Search across all cached transcripts.

    Args:
        query: Search string (case-insensitive).
        limit: Max results to return.

    Returns:
        List of dicts with video_id, language, matching segments.
    """
    conn = _get_db()
    try:
        # FTS search on full_text
        rows = conn.execute(
            """SELECT t.video_id, t.language, t.segments_json, t.full_text,
                      COALESCE(m.title, t.video_id) as title
               FROM transcripts t
               LEFT JOIN video_metadata m ON t.video_id = m.video_id
               WHERE t.full_text LIKE ?
               LIMIT ?""",
            (f"%{query}%", limit),
        ).fetchall()

        results = []
        for row in rows:
            segments = json.loads(row["segments_json"])
            # Find matching segments
            matching = [
                s for s in segments
                if query.lower() in s.get("text", "").lower()
            ]
            results.append({
                "video_id": row["video_id"],
                "title": row["title"],
                "language": row["language"],
                "match_count": len(matching),
                "segments": matching[:10],  # cap per video
            })
        return results
    finally:
        conn.close()

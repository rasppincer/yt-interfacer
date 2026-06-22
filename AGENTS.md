# AGENTS.md — yt-interfacer

## Project Context

YouTube API server — transcripts and MP3 archival. Flask-based, API-only (no frontend).

## Conventions

- **Language:** Python 3.10+
- **Framework:** Flask
- **Package manager:** uv (preferred) or pip with venv
- **Testing:** pytest
- **Linting:** ruff

## File Layout

```
yt-interfacer/
├── src/yt_interfacer/
│   ├── __init__.py
│   ├── app.py          # Flask app + routes
│   ├── transcript.py   # youtube-transcript-api wrapper
│   └── archiver.py     # yt-dlp MP3 download + archive management
├── tests/
├── archive/            # Downloaded MP3s (gitignored)
├── pyproject.toml
└── TODO.md
```

## Port

8323 (nginx proxy at /yt-interfacer/)

## Working Style

- Research before code. Spike before commit.
- Small commits, descriptive messages
- Tests for API endpoints

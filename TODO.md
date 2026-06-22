# TODO

## Phase 1: Core
- [x] Scaffold project (pyproject, venv, deps)
- [x] transcript.py — fetch YouTube transcripts
- [x] archiver.py — yt-dlp MP3 download + archive
- [x] app.py — Flask API endpoints
- [x] Systemd service + nginx proxy

## Phase 2: Enhancements
- [x] Bulk archive — playlist support (`GET /api/playlist`, `POST /api/playlist/archive`) — lists + downloads all videos in a YouTube playlist
- [x] Transcript search — search within cached transcripts (`GET /api/search?q=...`) with timestamp matches
- [x] Metadata cache — SQLite cache for video metadata + transcripts (`GET /api/metadata`, `GET /api/metadata/<id>`)

## Phase 3: Channel transcripts
- [x] `channel.py` — list channel videos via yt-dlp + archive transcripts as .txt files
- [x] `GET /api/channel?url=...` — list videos in a channel
- [x] `POST /api/channel/archive` — archive all transcripts from a channel
- [x] `GET /api/channel/transcripts` — list archived transcript files

## Phase 4: Data hygiene
- [x] Move archive/ and transcripts/ out of project dir — data shouldn't live in the git repo structure
  - Moved to ~/data/yt-interfacer/ (1.2GB archive, 1.4MB transcripts)
  - Symlinks in project dir keep code working without changes
  - Repo lightweight for GitHub push
- [ ] Initialize git repo + push to GitHub (after data is moved out)

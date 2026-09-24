# yt-migrate-watch-history

A minimal, production-grade **Python + Playwright** automation that replays a YouTube watch history from a local JSON file — one video at a time, dwelling a randomized **20–45 seconds** on each — and records every completion in a local **SQLite** database, so an interrupted run resumes exactly where it left off.

**Single file. No classes. No ORM. No dependencies beyond `playwright`.**

## Documentation

Full docs (prerequisites, setup, usage, runtime estimates, design notes, FAQ) live in [`site/`](site/):

```bash
cd site && python -m http.server 8080
# open http://localhost:8080
```

## Quickstart

```bash
# 1. Prerequisites (once)
python3 -m venv .venv && source .venv/bin/activate
pip install playwright
playwright install chromium

# 2. Put your history next to the script:
#    scraped_history.json -> JSON array of 18,453 full video URLs
#    (see scraped_history.example.json for the exact shape)

# 3. Run
python migrate_watch_history.py
```

On first run the script opens a browser and prompts you in the terminal to **log in to YouTube manually**. The session persists in `chrome_profile/`, so later runs never prompt again.

## Files

| Path | Purpose | Committed? |
| --- | --- | --- |
| `migrate_watch_history.py` | The entire automation (~180 lines) | yes |
| `scraped_history.json` | Input: JSON array of video URLs (your data) | no (gitignored) |
| `scraped_history.example.json` | Shape reference | yes |
| `state.db` | SQLite progress store (`watched_videos` table) | no (gitignored) |
| `chrome_profile/` | Persistent Chromium profile (keeps login) | no (gitignored) |
| `site/` | Documentation website | yes |

## Resuming & fixing

- **Interrupt with `Ctrl+C` any time.** Each URL is committed to `state.db` immediately after its dwell completes, so a re-run skips everything already done.
- **Failed URLs are marked as attempted** (committed, with a warning logged) so one dead link can never stall a multi-day run. To retry one:

  ```bash
  sqlite3 state.db "DELETE FROM watched_videos WHERE url = 'https://www.youtube.com/watch?v=...';"
  ```

## Notes

- 18,453 videos × 20–45 s dwell ≈ **4.3–9.6 days** of wall time (≈ 7 days typical at 32.5 s average), plus navigation overhead. Leave the machine on.
- Navigation deliberately uses `wait_until="domcontentloaded"` — `networkidle` times out on YouTube because streaming and telemetry keep the network busy.
- Audio is muted at the browser level (`--mute-audio`); a small JS snippet nudges the player into playback if it starts paused.

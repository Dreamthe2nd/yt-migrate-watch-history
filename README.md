# yt-migrate-watch-history

A minimal, production-grade **Python + Playwright** automation that replays a YouTube watch history from a local JSON file — one video at a time, dwelling a randomized **20–45 seconds** on each — and records every completion in a local **SQLite** database, so the run resumes from the next unwatched video after **any** closure: Ctrl+C, `SIGTERM`, crash, power loss, or a routine stop.

**Single file. No classes. No ORM. No dependencies beyond `playwright`.**

## Documentation

Full docs (prerequisites, setup, usage, runtime estimates, design notes, FAQ) live in [`site/`](site/):

```bash
cd site && python -m http.server 8080
# open http://localhost:8080
```

## Install (one command)

```bash
# Linux / macOS
./install.sh

# Windows
install.bat
```

Each installer creates a `.venv`, installs `playwright`, and downloads the bundled Chromium. Manual equivalent:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Usage

1. Place **your** `scraped_history.json` (a JSON array of full video URLs — e.g. the 18,453-entry list included in this repo) next to the script. It is the script's **sole input** and is never generated, sampled, or fabricated by the project. (`scraped_history.example.json` is a shape reference only — the script never reads it.)
2. Run:

   ```bash
   python migrate_watch_history.py
   ```

3. **Stage 1 — login (Chrome):** on a fresh run (0 completed videos) your installed **Google Chrome** opens on the shared `./chrome_profile`; you log in once and press ENTER in the terminal. If Chrome isn't installed, the script falls back to Chromium for that one-time login.
4. **Stage 2 — playback (Chromium):** Playwright's bundled **Chromium** — muted via `--mute-audio` — takes over the same profile and replays the videos in reverse order (`index -1 → 0`).

## Safety & resume

- **Every URL is committed to `state.db` immediately after its dwell** — before the next URL starts. Re-run the script after any interruption and it automatically starts from the next unwatched video. `SIGTERM` is handled the same way as Ctrl+C.
- **Failed URLs are marked as attempted** (committed, with a warning logged) so one dead link can never stall a multi-day run. To retry one:

  ```bash
  sqlite3 state.db "DELETE FROM watched_videos WHERE url = 'https://www.youtube.com/watch?v=...';"
  ```

- **Browser hygiene:** the page is unloaded to `about:blank` after every video, and the whole browser process is recycled every 50 videos (`BROWSER_RESTART_EVERY`), keeping RAM/CPU flat over days of running. The persistent profile means the login survives each recycle.

## Files

| Path | Purpose | Committed? |
| --- | --- | --- |
| `migrate_watch_history.py` | The entire automation | yes |
| `install.sh` / `install.bat` / `requirements.txt` | One-command setup | yes |
| `scraped_history.json` | Input: JSON array of 18,453 video URLs (the sole input) | yes |
| `scraped_history.example.json` | Shape reference (never read by the script) | yes |
| `state.db` | SQLite progress store (`watched_videos` table) | no (gitignored) |
| `chrome_profile/` | Shared persistent profile (Chrome login + Chromium playback) | no (gitignored) |
| `site/` | Documentation website | yes |

## Notes

- 18,453 videos × 20–45 s dwell ≈ **4.3–9.6 days** of wall time (≈ 7 days typical), plus navigation overhead. Leave the machine on.
- Navigation deliberately uses `wait_until="domcontentloaded"` — `networkidle` times out on YouTube because streaming and telemetry keep the network busy.
- If the playback browser ever shows a login wall (rare; can happen on macOS due to per-browser cookie encryption), click through once — the session is then kept by the shared profile.

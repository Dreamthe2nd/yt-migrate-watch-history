#!/usr/bin/env python3
"""Replay and migrate a YouTube watch history from a local JSON file.

Reads   : scraped_history.json  (JSON array of video URLs)
State   : state.db              (sqlite3; table watched_videos(url, watched_at))
Profile : chrome_profile/       (persistent Chromium profile, keeps you logged in)

Behaviour:
  * URLs are processed in reverse order (original index -1 down to 0).
  * Each video page is dwelled on for a random 20-45 second duration.
  * Every URL is committed to state.db immediately after its watch duration
    completes, so an interrupted run (Ctrl+C, crash, power loss) resumes
    exactly where it left off.
  * A failed URL logs a warning and is still committed (marked "attempted")
    so one bad link can never stall an otherwise long-running migration.

Usage:
    python migrate_watch_history.py
"""

from __future__ import annotations

import json
import random
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
HISTORY_FILE = BASE_DIR / "scraped_history.json"
DB_FILE = BASE_DIR / "state.db"
PROFILE_DIR = BASE_DIR / "chrome_profile"

DWELL_MIN_S = 20
DWELL_MAX_S = 45
GO_TO_TIMEOUT_MS = 60_000
PLAYER_WAIT_MS = 5_000

# One-shot safety net: if the player is paused (autoplay blocked, overlay,
# tab hiccup), nudge it into playing. Audio is already muted via --mute-audio.
START_PLAYBACK_JS = """
    () => {
        const v = document.querySelector("video");
        if (v && v.paused) {
            v.muted = true;
            v.play().catch(() => {});
        }
    }
"""


def init_db() -> sqlite3.Connection:
    """Create state.db and the watched_videos table if they do not exist yet."""
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS watched_videos ("
        "url TEXT PRIMARY KEY, "
        "watched_at TIMESTAMP)"
    )
    conn.commit()
    return conn


def load_urls() -> list[str]:
    """Load the URL list and reverse it so we watch original index -1 -> 0."""
    if not HISTORY_FILE.exists():
        sys.exit(
            f"error: {HISTORY_FILE} not found.\n"
            "Create it as a JSON array of full video URLs, e.g.:\n"
            '    ["https://www.youtube.com/watch?v=..."]'
        )
    raw = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    urls = [u.strip() for u in raw if u and u.strip()]
    return list(reversed(urls))


def eta_label(remaining: int, avg_per_video_s: float) -> str:
    return f"ETA: ~{remaining * avg_per_video_s / 3600:.1f} hours"


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)  # keep telemetry live when piped

    urls = load_urls()
    conn = init_db()
    total = len(urls)
    print(f"Loaded {total} URLs from {HISTORY_FILE.name}; processing in reverse order (index -1 -> 0).")
    print(f"Dwell per video: {DWELL_MIN_S}-{DWELL_MAX_S}s (random). State: {DB_FILE.name}")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,  # visible window: required for the one-time manual login
            args=["--mute-audio"],
            viewport={"width": 1366, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()

        completed = conn.execute("SELECT COUNT(*) FROM watched_videos").fetchone()[0]
        if completed == 0:
            page.goto("https://www.youtube.com", wait_until="domcontentloaded")
            input(
                "\nstate.db is empty (0 completed videos).\n"
                "Log in to your YouTube account in the browser window,\n"
                "then press ENTER here to start the migration...\n"
            )
            print("Starting.\n")
        else:
            print(f"Resuming run: {completed} URLs already recorded in {DB_FILE.name}.\n")

        processed = 0
        watched = 0
        watched_seconds = 0.0

        try:
            for url in urls:
                processed += 1
                remaining = total - processed
                # Average observed per-watch time (dwell + navigation overhead);
                # fall back to the dwell midpoint until the first watch completes.
                avg_s = (watched_seconds / watched) if watched else (DWELL_MIN_S + DWELL_MAX_S) / 2

                if conn.execute("SELECT 1 FROM watched_videos WHERE url = ?", (url,)).fetchone():
                    print(
                        f"[{processed}/{total}] | Skipped (already in state.db) "
                        f"| Remaining: {remaining} | {eta_label(remaining, avg_s)}"
                    )
                    continue

                dwell_s = random.randint(DWELL_MIN_S, DWELL_MAX_S)
                t0 = time.monotonic()
                try:
                    # domcontentloaded, never networkidle: YouTube streaming and
                    # telemetry keep sockets open, so networkidle would time out.
                    page.goto(url, wait_until="domcontentloaded", timeout=GO_TO_TIMEOUT_MS)
                    try:
                        page.wait_for_selector("video", timeout=PLAYER_WAIT_MS)
                    except Exception:
                        pass  # no <video> (unavailable/age-gated) - still dwell on page
                    try:
                        page.evaluate(START_PLAYBACK_JS)
                    except Exception:
                        pass  # page may have navigated; don't fail the view over it
                    time.sleep(dwell_s)
                    elapsed_s = time.monotonic() - t0
                    watched += 1
                    watched_seconds += elapsed_s
                    print(
                        f"[{processed}/{total}] | Watched {elapsed_s:.0f}s "
                        f"| Remaining: {remaining} | {eta_label(remaining, avg_s)}"
                    )
                except Exception as exc:
                    first_line = str(exc).splitlines()[0] if str(exc) else "no message"
                    print(
                        f"[{processed}/{total}] | WARNING: view failed "
                        f"({exc.__class__.__name__}: {first_line}) - marked as attempted "
                        f"| Remaining: {remaining} | {eta_label(remaining, avg_s)}"
                    )

                # Commit immediately, success or failure: this URL will not be
                # revisited on resume (idempotent). Delete its row to retry it.
                conn.execute(
                    "INSERT OR IGNORE INTO watched_videos (url, watched_at) VALUES (?, ?)",
                    (url, datetime.now(timezone.utc).isoformat(timespec="seconds")),
                )
                conn.commit()
        except KeyboardInterrupt:
            print("\nInterrupted. All completed views are saved in state.db - re-run to resume.")
        finally:
            context.close()

    final = conn.execute("SELECT COUNT(*) FROM watched_videos").fetchone()[0]
    conn.close()
    print(f"\nDone. {final}/{total} URLs recorded in {DB_FILE.name}.")


if __name__ == "__main__":
    main()

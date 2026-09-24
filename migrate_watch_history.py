#!/usr/bin/env python3
"""Replay and migrate a YouTube watch history from a local JSON file.

Input   : scraped_history.json  (JSON array of video URLs) -- the SOLE input.
          The script never generates, samples, or fabricates data.
State   : state.db              (sqlite3; table watched_videos(url, watched_at))
Profile : chrome_profile/       (shared persistent profile)

Two-stage browser design:
  1. LOGIN    - the user's Google Chrome (channel="chrome") opens on the
                shared profile; you log in once, the terminal confirms.
  2. PLAYBACK - Playwright's bundled Chromium (muted via --mute-audio) opens
                the SAME profile and does the multi-day replay, so playback
                is isolated from the user's everyday browser.

Reliability:
  * URLs are processed in reverse order (original index -1 down to 0).
  * Each video page is dwelled on for a random 20-45 second duration.
  * Every URL is committed to state.db immediately after its watch duration
    completes - before the next URL starts. On ANY closure (Ctrl+C, SIGTERM,
    crash, power loss, routine stop) the next run automatically starts from
    the next unwatched video.
  * A failed URL logs a warning and is still committed (marked "attempted")
    so one bad link can never stall a multi-day migration.
  * Browser hygiene: the page is unloaded to about:blank after every video,
    and the whole browser process is recycled every 50 videos, so RAM and
    CPU stay flat over days of continuous running.

Usage:
    python migrate_watch_history.py
"""

from __future__ import annotations

import json
import random
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

BASE_DIR = Path(__file__).resolve().parent
HISTORY_FILE = BASE_DIR / "scraped_history.json"  # sole input - never fabricated
DB_FILE = BASE_DIR / "state.db"
PROFILE_DIR = BASE_DIR / "chrome_profile"         # shared by Chrome (login) + Chromium (playback)

DWELL_MIN_S = 20
DWELL_MAX_S = 45
GO_TO_TIMEOUT_MS = 60_000
PLAYER_WAIT_MS = 5_000
BROWSER_RESTART_EVERY = 50  # full browser recycle (RAM/CPU reset) every N watched videos

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
    """Read the SOLE input file and reverse it (process original index -1 -> 0)."""
    if not HISTORY_FILE.exists():
        sys.exit(
            f"error: {HISTORY_FILE} not found.\n"
            "Place your scraped history file (a JSON array of full video URLs)\n"
            "in this folder and re-run. The script never generates data itself."
        )
    raw = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    urls = [u.strip() for u in raw if u and u.strip()]
    return list(reversed(urls))


def open_browser(pw: Playwright, *, chrome: bool = False, muted: bool = False):
    """Launch a persistent-context browser on the shared ./chrome_profile.

    chrome=True  -> the user's installed Google Chrome (login stage)
    chrome=False -> Playwright's bundled Chromium (playback stage)
    """
    kwargs: dict = {
        "user_data_dir": str(PROFILE_DIR),
        "headless": False,
        "viewport": {"width": 1366, "height": 900},
    }
    if chrome:
        kwargs["channel"] = "chrome"
    if muted:
        kwargs["args"] = ["--mute-audio"]
    context = pw.chromium.launch_persistent_context(**kwargs)
    page = context.pages[0] if context.pages else context.new_page()
    return context, page


def ensure_login(pw: Playwright) -> None:
    """Stage 1: one-time manual login, handled by the user's Chrome."""
    print("Stage 1 (login): opening Google Chrome on the shared profile...")
    try:
        context, page = open_browser(pw, chrome=True)
    except Exception as exc:
        print(f"  Chrome not available ({exc.__class__.__name__}) - "
              f"falling back to Chromium for the one-time login.")
        context, page = open_browser(pw)
    try:
        page.goto("https://www.youtube.com", wait_until="domcontentloaded")
        input(
            "\nLog in to your YouTube account in the browser window,\n"
            "then press ENTER here. (Already logged in? Just press ENTER.)\n"
            "The session is saved in ./chrome_profile and reused by the\n"
            "playback stage (Chromium) automatically.\n"
        )
    finally:
        context.close()
    print("Stage 1 complete - session stored in the shared profile.\n")


def close_quiet(context: BrowserContext) -> None:
    try:
        context.close()
    except Exception:
        pass


def eta_label(remaining: int, avg_per_video_s: float) -> str:
    return f"ETA: ~{remaining * avg_per_video_s / 3600:.1f} hours"


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)  # keep telemetry live when piped

    # Routine/system closure (SIGTERM) takes the same safe path as Ctrl+C:
    # cleanup runs, and the next run resumes from the next unwatched video.
    def _terminate(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _terminate)

    urls = load_urls()
    conn = init_db()
    total = len(urls)
    print(f"Loaded {total} URLs from {HISTORY_FILE.name}; processing in reverse order (index -1 -> 0).")
    print(f"Dwell per video: {DWELL_MIN_S}-{DWELL_MAX_S}s (random). State: {DB_FILE.name}. "
          f"Browser recycled every {BROWSER_RESTART_EVERY} videos.")

    with sync_playwright() as pw:
        completed = conn.execute("SELECT COUNT(*) FROM watched_videos").fetchone()[0]
        if completed == 0:
            ensure_login(pw)
        else:
            print(f"Resuming run: {completed} URLs already recorded in {DB_FILE.name} - "
                  f"starting from the next unwatched video.\n")

        # Stage 2: playback by Playwright's bundled Chromium on the same profile.
        print(f"Stage 2 (playback): starting Chromium (muted) on profile {PROFILE_DIR.name}...")
        context, page = open_browser(pw, muted=True)

        processed = 0
        watched = 0
        watched_seconds = 0.0
        since_restart = 0

        try:
            for url in urls:
                processed += 1
                remaining = total - processed
                # Average observed per-watch time (dwell + overhead); fall back
                # to the dwell midpoint until the first watch completes.
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
                    outcome = f"Watched {elapsed_s:.0f}s"
                except Exception as exc:
                    first_line = str(exc).splitlines()[0] if str(exc) else "no message"
                    outcome = f"WARNING: view failed ({exc.__class__.__name__}: {first_line}) - marked as attempted"

                # Commit FIRST: this URL is done (success or attempted) before
                # anything else touches the browser. This is what makes every
                # closure - system or routine - resume from the next video.
                conn.execute(
                    "INSERT OR IGNORE INTO watched_videos (url, watched_at) VALUES (?, ?)",
                    (url, datetime.now(timezone.utc).isoformat(timespec="seconds")),
                )
                conn.commit()

                print(
                    f"[{processed}/{total}] | {outcome} "
                    f"| Remaining: {remaining} | {eta_label(remaining, avg_s)}"
                )

                # Refresh (per video): unload the page so its video buffers and
                # memory are released before the next navigation.
                try:
                    page.goto("about:blank", wait_until="domcontentloaded")
                except Exception:
                    pass

                # Refresh (periodic): recycle the whole browser process so RAM
                # and CPU stay flat over multi-day runs. The persistent profile
                # means the login survives the recycle untouched.
                since_restart += 1
                if since_restart >= BROWSER_RESTART_EVERY:
                    print(f"> Browser recycled after {since_restart} videos (RAM/CPU reset).")
                    close_quiet(context)
                    context, page = open_browser(pw, muted=True)
                    since_restart = 0
        except KeyboardInterrupt:
            print("\nClosure handled. All completed views are saved in state.db - "
                  "re-run to automatically start from the next unwatched video.")
        finally:
            close_quiet(context)

    final = conn.execute("SELECT COUNT(*) FROM watched_videos").fetchone()[0]
    conn.close()
    print(f"\nDone. {final}/{total} URLs recorded in {DB_FILE.name}.")


if __name__ == "__main__":
    main()

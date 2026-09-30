#!/usr/bin/env python3
"""Replay and migrate a YouTube watch history from a local JSON file.

Input   : scraped_history.json  (JSON array of video URLs) -- the SOLE input.
          The script never generates, samples, or fabricates data.
State   : state.db              (sqlite3; table watched_videos(url, watched_at))
Profile : chrome_profile/       (shared persistent profile)

Two-stage browser design:
  1. LOGIN    - the user's installed Google Chrome is started as a PLAIN OS
                process (NO Playwright/CDP automation attached -- Google
                rejects sign-in from automated browsers with "This browser
                or app may not be secure"). You sign in once, close that
                window, and the session is stored in ./chrome_profile.
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
    python migrate_watch_history.py --login   # force Stage 1 again (e.g. after a blocked sign-in)
"""

from __future__ import annotations

import json
import os
import random
import shutil
import signal
import sqlite3
import subprocess
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


def find_chrome_executable() -> str | None:
    """Locate the user's installed Google Chrome binary (plain, not via Playwright)."""
    env = os.environ
    candidates: list[Path] = []
    if sys.platform.startswith("win"):
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "LOCALAPPDATA"):
            base = env.get(var)
            if base:
                candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
        candidates.append(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    else:
        for name in ("google-chrome", "google-chrome-stable"):
            found = shutil.which(name)
            if found:
                return found
        candidates.append(Path("/opt/google/chrome/google-chrome"))
    seen: set[str] = set()
    for cand in candidates:
        key = str(cand)
        if key not in seen and cand.exists():
            return key
        seen.add(key)
    return None


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


def open_browser(pw: Playwright, *, muted: bool = False, login: bool = False):
    """Launch Playwright's bundled Chromium on the shared ./chrome_profile.

    muted -> add --mute-audio (playback stage)
    login -> soften the automation fingerprint for the no-Chrome login fallback
    Retries in case the Stage-1 Chrome window just closed and the profile
    lock has not been released yet.
    """
    args: list[str] = []
    if muted:
        args.append("--mute-audio")
    if login:
        args.append("--disable-blink-features=AutomationControlled")
    kwargs: dict = {
        "user_data_dir": str(PROFILE_DIR),
        "headless": False,
        "viewport": {"width": 1366, "height": 900},
    }
    if args:
        kwargs["args"] = args
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            context = pw.chromium.launch_persistent_context(**kwargs)
            page = context.pages[0] if context.pages else context.new_page()
            return context, page
        except Exception as exc:
            last_exc = exc
            if attempt == 2:
                raise
            first = str(exc).splitlines()[0] if str(exc) else "no message"
            print(f"  Browser could not start ({exc.__class__.__name__}: {first}). "
                  f"Close any leftover Stage-1 Chrome window - retrying in 3s...")
            time.sleep(3)
    raise last_exc  # unreachable


def ensure_login(pw: Playwright) -> None:
    """Stage 1: one-time manual login in the user's REAL Chrome.

    Chrome is launched as a plain OS process with NO Playwright/CDP attached:
    Google blocks sign-in from automation-attached browsers ("This browser
    or app may not be secure"). The session lands in ./chrome_profile, which
    Stage 2's Chromium reuses. Use --login to force this stage again.
    """
    chrome = find_chrome_executable()
    if chrome:
        print("Stage 1 (login): opening your Google Chrome (plain window, no automation)...")
        print("  1. Sign in to YouTube in that Chrome window.")
        print("  2. CLOSE the window when done - the script continues automatically.")
        proc = subprocess.Popen(
            [
                chrome,
                f"--user-data-dir={PROFILE_DIR}",
                "--no-first-run",
                "--no-default-browser-check",
                "https://www.youtube.com",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            proc.wait()  # waits for the Chrome window to be closed; Ctrl+C works here
        except KeyboardInterrupt:
            proc.terminate()
            raise
        time.sleep(2)  # let Chrome release the profile lock
        print("Stage 1 complete - session stored in the shared profile.\n")
        return

    # No system Chrome: fall back to Playwright's Chromium (may be blocked by Google).
    print("Stage 1 (login): Google Chrome not found on this system.")
    print("  Falling back to Playwright's Chromium. Google often refuses sign-in")
    print('  there ("This browser or app may not be secure"). If that happens,')
    print("  install Google Chrome and re-run - Stage 1 will then use it.")
    context, page = open_browser(pw, login=True)
    try:
        page.goto("https://www.youtube.com", wait_until="domcontentloaded")
        input(
            "\nTry to sign in in the browser window (it may be blocked by Google),\n"
            "then press ENTER here.\n"
        )
    finally:
        close_quiet(context)
    print("Stage 1 done.\n")


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
    force_login = "--login" in sys.argv[1:]
    print(f"Loaded {total} URLs from {HISTORY_FILE.name}; processing in reverse order (index -1 -> 0).")
    print(f"Dwell per video: {DWELL_MIN_S}-{DWELL_MAX_S}s (random). State: {DB_FILE.name}. "
          f"Browser recycled every {BROWSER_RESTART_EVERY} videos.")

    with sync_playwright() as pw:
        completed = conn.execute("SELECT COUNT(*) FROM watched_videos").fetchone()[0]
        if completed == 0 or force_login:
            if completed:
                print(f"--login: forcing Stage 1 before resuming {completed} recorded videos.")
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
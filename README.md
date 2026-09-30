# YouTube Watch History Migrator

A cross-platform automation utility designed to migrate YouTube watch history from one account to another. Replays historical video links in chronological sequence using Playwright and an SQLite state engine to seamlessly reconstruct recommendation embeddings on a new profile[cite: 1].

---

## Features

- **Algorithmic Chronology:** Iterates backward through exported history (from oldest to newest) to preserve the natural evolution of feed recommendations[cite: 1].
- **Two-Stage Authentication:** Uses a native, unautomated browser instance for initial Google sign-in to bypass bot detection, storing credentials in a persistent profile.
- **Resilient State Tracking:** Uses SQLite (`state.db`) to record each completed video before advancing. Survives Ctrl+C, power outages, and scheduled system reboots without duplicate attempts[cite: 1].
- **Anti-Bot & Memory Hygiene:** Features randomized dwell times, muted audio (`--mute-audio`), automatic `about:blank` cache resets, and periodic browser process recycling every 50 videos[cite: 1].
- **Cross-Platform:** Out-of-the-box support for Windows, macOS, and Linux[cite: 1].

---

## Prerequisites

- **Python:** 3.8 or newer[cite: 1]
- **Google Chrome:** Installed on the host system (required for Stage-1 safe authentication)

---

## Quick Start

### 1. Extract Your Watch History
1. Log into your source account and navigate to [myactivity.google.com](https://myactivity.google.com).
2. Filter or scroll down to the history range you wish to migrate.
3. Open your browser's Developer Tools Console (`F12` or `Ctrl+Shift+I` / `Cmd+Option+I`) and paste:

```javascript
(() => {
  const links = Array.from(document.querySelectorAll('a[href*="[youtube.com/watch](https://youtube.com/watch)"], a[href*="youtu.be"]'))
    .map(a => a.href.split('&')[0])
    .filter((url, idx, arr) => arr.indexOf(url) === idx && !url.includes('/channel/') && !url.includes('/post/'));

  if (links.length === 0) {
    console.warn("No links detected. Scroll up/down slightly or check if cards are rendered.");
    return;
  }

  const blob = new Blob([JSON.stringify(links, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'scraped_history.json';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);

  console.log(`Exported ${links.length} clean video URLs!`);
})();
```
Place the downloaded scraped_history.json file directly into the repository root.

Here is a comprehensive, production-ready `README.md` structured for your repository:

```markdown
# YouTube Watch History Migrator

A cross-platform automation utility designed to migrate YouTube watch history from one account to another[cite: 1]. Replays historical video links in chronological sequence using Playwright and an SQLite state engine to seamlessly reconstruct recommendation embeddings on a new profile[cite: 1].

---

## Features

- **Algorithmic Chronology:** Iterates backward through exported history (from oldest to newest) to preserve the natural evolution of feed recommendations[cite: 1].
- **Two-Stage Authentication:** Uses a native, unautomated browser instance for initial Google sign-in to bypass bot detection, storing credentials in a persistent profile.
- **Resilient State Tracking:** Uses SQLite (`state.db`) to record each completed video before advancing. Survives Ctrl+C, power outages, and scheduled system reboots without duplicate attempts[cite: 1].
- **Anti-Bot & Memory Hygiene:** Features randomized dwell times, muted audio (`--mute-audio`), automatic `about:blank` cache resets, and periodic browser process recycling every 50 videos[cite: 1].
- **Cross-Platform:** Out-of-the-box support for Windows, macOS, and Linux[cite: 1].

---

## Prerequisites

- **Python:** 3.8 or newer[cite: 1]
- **Google Chrome:** Installed on the host system (required for Stage-1 safe authentication)

---

## Quick Start

### 1. Extract Your Watch History
1. Log into your source account and navigate to [myactivity.google.com](https://myactivity.google.com).
2. Filter or scroll down to the history range you wish to migrate.
3. Open your browser's Developer Tools Console (`F12` or `Ctrl+Shift+I` / `Cmd+Option+I`) and paste:

```javascript
(() => {
  const links = Array.from(document.querySelectorAll('a[href*="[youtube.com/watch](https://youtube.com/watch)"], a[href*="youtu.be"]'))
    .map(a => a.href.split('&')[0])
    .filter((url, idx, arr) => arr.indexOf(url) === idx && !url.includes('/channel/') && !url.includes('/post/'));

  if (links.length === 0) {
    console.warn("No links detected. Scroll up/down slightly or check if cards are rendered.");
    return;
  }

  const blob = new Blob([JSON.stringify(links, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'scraped_history.json';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);

  console.log(`Exported ${links.length} clean video URLs!`);
})();

```

4. Place the downloaded `scraped_history.json` file directly into the repository root.

---

### 2. Installation

Clone the repository and run the automated installer for your OS:

#### Windows

```powershell
git clone [https://github.com/Dreamthe2nd/yt-migrate-watch-history.git](https://github.com/Dreamthe2nd/yt-migrate-watch-history.git)
cd yt-migrate-watch-history
.\install.bat

```

#### Linux & macOS

```bash
git clone [https://github.com/Dreamthe2nd/yt-migrate-watch-history.git](https://github.com/Dreamthe2nd/yt-migrate-watch-history.git)
cd yt-migrate-watch-history
chmod +x install.sh
./install.sh

```

The installer provisions a Python virtual environment, installs dependencies, and downloads Playwright's Chromium binaries automatically.

---

### 3. Execution

Activate the virtual environment and start the migration:

```bash
# Windows
.\venv\Scripts\activate
python migrate_watch_history.py

# Linux / macOS
source venv/bin/activate
python3 migrate_watch_history.py

```

#### Workflow Stages:

1. **Stage 1 (One-Time Login):** A standard Google Chrome window opens without automation hooks. Log into the destination Google/YouTube account and manually close the window when finished.
2. **Stage 2 (Automated Playback):** Playwright launches Chromium using the authenticated profile, muting audio and sequentially playing each video in your queue.

---

## Configuration

Adjust the execution variables inside `migrate_watch_history.py` to tailor performance to your machine:

| Parameter | Default | Description |
| --- | --- | --- |
| `DWELL_MIN_S` | `20` | Minimum dwell time per video in seconds |
| `DWELL_MAX_S` | `45` | Maximum dwell time per video in seconds |
| `BROWSER_RESTART_EVERY` | `50` | Recycles the browser process to clear Chromium heap memory |
| `GO_TO_TIMEOUT_MS` | `60000` | Navigation timeout threshold before logging an attempted state |

To re-authenticate or switch destination accounts, run with the `--login` flag:

```bash
python migrate_watch_history.py --login

```

---

## Scheduled Reboots & Unattended Operation

If processing large datasets (10,000+ entries), you can run the script via an automated wrapper that catches periodic batch exits to flush the host OS memory cache and restart the runner.

### PowerShell Wrapper (`run_batch.ps1`)

```powershell
& ".\venv\Scripts\python.exe" "migrate_watch_history.py"

if ($LASTEXITCODE -eq 42) {
    Write-Host "Batch threshold met. Rebooting in 15 seconds..." -ForegroundColor Cyan
    shutdown /r /t 15 /c "Scheduled browser cache reset"
}

```

---

## Architecture Overview

```
yt-migrate-watch-history/
├── chrome_profile/           # Shared persistent profile directory (session state)
├── scraped_history.json      # Primary URL queue (extracted from MyActivity)
├── state.db                  # SQLite database tracking processed URLs and timestamps
├── migrate_watch_history.py  # Core playback engine and lifecycle manager
├── install.bat               # Windows environment provisioner
└── install.sh                # Unix environment provisioner

```

---

## License

This project is licensed under the MIT License.

```

```

# Sock-Puppet Audit of YouTube & TikTok

Automates persona-driven watch sessions on YouTube and TikTok using Playwright (headful or headless) to mimic naturalistic behavior: randomized dwell times, occasional scrolls, and persistent browser profiles per persona. Logs watched videos and sidebar/feed recommendations to CSV for downstream analysis.

## Features
- **Personas** with seed keywords and per-platform toggles
- **Daily sessions**: default ~50 videos per persona per day
- **Randomized dwell**: watch time ranges to approximate real viewing
- **Persistent profiles** (`user_data_dir`) to keep history per account
- **CSV logs** for watched items and recommendations
- **Dry-run** option (no clicks/likes) for safe testing

> ⚠️ Ethics & Terms: Use responsibly and only on test/sock accounts per your IRB/ToS constraints.

## Quick Start

### 1) Environment
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

### 2) Configure personas
Edit `personas/personas.yaml`:
```yaml
personas:
  - name: "politics_left_01"
    platform: ["youtube","tiktok"]
    keywords: ["election fraud debunked", "progressive policies"]
    videos_per_day: 50
    user_data_dir: "./profiles/politics_left_01"   # created on first run
  - name: "fitness_01"
    platform: ["youtube","tiktok"]
    keywords: ["home workouts", "10 min abs"]
    videos_per_day: 50
    user_data_dir: "./profiles/fitness_01"
```

### 3) Run (YouTube)
```bash
python youtube/simple_watch_YT.py --persona politics_left_01 --days 1
```

### 4) Run (TikTok)
```bash
python tiktok/simple_watch_TT_v4.py --persona fitness_01 --days 1
```

### 5) Outputs
CSV logs in `./data/logs/<platform>/<persona>/YYYY-MM-DD/*.csv`:
- `watched.csv`: one row per watched video
- `recs.csv`: recommendations captured during the watch

## Notes
- To simulate longer “full” watches, increase `--dwell-max`.
- To keep sessions human-like, scripts add jitter and intermittent pauses.
- Login is optional. If you need logged-in behavior, sign in once in the launched profile window; cookies persist via `user_data_dir`.

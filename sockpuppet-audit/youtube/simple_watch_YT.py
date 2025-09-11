import argparse, time, random, re
import pandas as pd
from pathlib import Path
from playwright.sync_api import sync_playwright
from common import rand_dwell, out_paths, write_rows, ts, ensure_dir

def clean_time_to_secs(txt):
    # Formats like 12:34 or 1:02:03
    parts = [int(p) for p in txt.strip().split(":")]
    if len(parts) == 3:
        h,m,s = parts
        return h*3600 + m*60 + s
    if len(parts) == 2:
        m,s = parts
        return m*60 + s
    return 0

def run_session(persona, keywords, videos_per_day, user_data_dir,
                dwell_min=20, dwell_max=90, headless=False, dry_run=False):
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(user_data_dir=user_data_dir, headless=headless)
        page = browser.new_page()
        watched_path, recs_path = out_paths("youtube", persona)

        # Choose a seed query for this session
        query = random.choice(keywords)
        page.goto(f"https://www.youtube.com/results?search_query={query}", timeout=120000)
        page.wait_for_selector("ytd-video-renderer,ytd-rich-item-renderer", timeout=120000)

        # Click the first reasonable video
        page.click("ytd-video-renderer a#thumbnail >> nth=0")
        page.wait_for_selector(".html5-video-player", timeout=120000)

        total = 0
        while total < videos_per_day:
            time.sleep(2)
            # Fetch metadata
            title = page.title()
            vid_id = page.url.split("v=")[-1].split("&")[0] if "watch?v=" in page.url else page.url
            try:
                dur_txt = page.locator(".ytp-time-duration").inner_text(timeout=5000)
                duration = clean_time_to_secs(dur_txt)
            except:
                duration = 0

            dwell = rand_dwell(dwell_min, dwell_max)
            if duration and dwell > duration: dwell = int(0.9*duration)  # cap to 90%

            # Collect sidebar recs
            recs = []
            sidebar = page.locator("ytd-watch-next-secondary-results-renderer #contents a#thumbnail")
            n = min(sidebar.count(), 20) if sidebar.count() else 0
            for i in range(n):
                try:
                    a = sidebar.nth(i)
                    href = a.get_attribute("href") or ""
                    rec_vid = href.split("v=")[-1].split("&")[0] if "watch?v=" in href else href
                    recs.append({"ts": ts(), "persona": persona, "seed_query": query, "watching": vid_id, "rec_vid": rec_vid})
                except:
                    pass
            if recs:
                write_rows(recs_path, ["ts","persona","seed_query","watching","rec_vid"], recs)

            # Watch
            if not dry_run:
                try:
                    page.keyboard.press("k")  # ensure playing
                except: pass
                time.sleep(dwell)

            # Move to a recommendation (first item)
            try:
                sidebar.first.click()
                page.wait_for_timeout(1000)
            except:
                # Fallback: go to homepage
                page.goto("https://www.youtube.com", timeout=120000)
                page.wait_for_selector("ytd-rich-item-renderer", timeout=120000)
                page.click("ytd-rich-item-renderer a#thumbnail >> nth=0")

            # Log watched
            write_rows(watched_path,
                       ["ts","persona","seed_query","video_id","title","dwell_secs","duration_secs"],
                       [{
                           "ts": ts(), "persona": persona, "seed_query": query,
                           "video_id": vid_id, "title": title, "dwell_secs": dwell, "duration_secs": duration
                        }])
            total += 1

        browser.close()

if __name__ == "__main__":
    import yaml, os
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", required=True)
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--dwell-min", type=int, default=20)
    ap.add_argument("--dwell-max", type=int, default=90)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open("personas/personas.yaml","r") as f:
        cfg = yaml.safe_load(f)

    pmap = {p["name"]: p for p in cfg["personas"]}
    if args.persona not in pmap:
        raise SystemExit(f"Unknown persona {args.persona}.")

    p = pmap[args.persona]
    ensure_dir(p["user_data_dir"])
    for _ in range(args.days):
        run_session(p["name"], p["keywords"], p["videos_per_day"],
                    p["user_data_dir"], args.dwell_min, args.dwell_max,
                    headless=args.headless, dry_run=args.dry_run)

import argparse, time, random, urllib.parse
from playwright.sync_api import sync_playwright
from common import rand_dwell, out_paths, write_rows, ts, ensure_dir

def run_session(persona, keywords, videos_per_day, user_data_dir,
                dwell_min=10, dwell_max=45, headless=False, dry_run=False):
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(user_data_dir=user_data_dir, headless=headless)
        page = browser.new_page()
        watched_path, recs_path = out_paths("tiktok", persona)

        query = random.choice(keywords)
        page.goto("https://www.tiktok.com", timeout=120000)
        page.wait_for_timeout(2000)
        page.goto(f"https://www.tiktok.com/search?q={urllib.parse.quote(query)}", timeout=120000)
        page.wait_for_selector('[data-e2e="search_top-item"] a', timeout=120000)

        # open first search result
        page.click('[data-e2e="search_top-item"] a >> nth=0')

        total = 0
        while total < videos_per_day:
            time.sleep(2)
            url = page.url
            vid_id = url.rstrip("/").split("/")[-1]
            try:
                title = page.locator('h1[data-e2e="browse-video-desc"]').inner_text(timeout=4000)
            except:
                title = ""

            dwell = rand_dwell(dwell_min, dwell_max)
            if not dry_run:
                time.sleep(dwell)

            # Collect feed recs (right sidebar / next items)
            recs = []
            try:
                cards = page.locator('[data-e2e="recommend-list-item"] a')
                n = min(cards.count(), 20) if cards.count() else 0
                for i in range(n):
                    try:
                        href = cards.nth(i).get_attribute("href") or ""
                        rec_id = href.rstrip("/").split("/")[-1]
                        recs.append({"ts": ts(), "persona": persona, "seed_query": query, "watching": vid_id, "rec_vid": rec_id})
                    except:
                        pass
                if recs:
                    write_rows(recs_path, ["ts","persona","seed_query","watching","rec_vid"], recs)
            except:
                pass

            # Go to the next video via keyboard (simulate natural scroll)
            try:
                page.keyboard.press("PageDown")
                page.wait_for_timeout(1200)
            except:
                # fallback: click first recommended
                try:
                    cards.first.click()
                except:
                    page.goto("https://www.tiktok.com", timeout=120000)

            # Log watched
            write_rows(watched_path,
                       ["ts","persona","seed_query","video_id","title","dwell_secs"],
                       [{
                           "ts": ts(), "persona": persona, "seed_query": query,
                           "video_id": vid_id, "title": title, "dwell_secs": dwell
                        }])
            total += 1

        browser.close()

if __name__ == "__main__":
    import yaml
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", required=True)
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--dwell-min", type=int, default=10)
    ap.add_argument("--dwell-max", type=int, default=45)
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

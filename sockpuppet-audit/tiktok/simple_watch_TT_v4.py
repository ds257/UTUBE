import argparse
import csv
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import WebDriverException, TimeoutException, JavascriptException

COOKIES_PATH = "cookies.json"
LSTORAGE_PATH = "localstorage.json"


# -------------------------- utils --------------------------
def human_sleep(a=0.8, b=1.6):
    time.sleep(random.uniform(a, b))


def ensure_parent(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def start_driver(headless: bool):
    from selenium.webdriver.chrome.options import Options

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1440,1000")
    options.add_argument("--lang=en-US,en;q=0.9")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-features=IsolateOrigins,site-per-process")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.set_page_load_timeout(60)
    return driver


def save_cookies_and_storage(driver, cookies_path=COOKIES_PATH, lstorage_path=LSTORAGE_PATH):
    ensure_parent(cookies_path)
    with open(cookies_path, "w", encoding="utf-8") as f:
        json.dump(driver.get_cookies(), f, indent=2)

    try:
        ls = driver.execute_script(
            "var s={}; for (var i=0;i<localStorage.length;i++){var k=localStorage.key(i); s[k]=localStorage.getItem(k);} return s;"
        )
    except JavascriptException:
        ls = {}
    ensure_parent(lstorage_path)
    with open(lstorage_path, "w", encoding="utf-8") as f:
        json.dump(ls, f, indent=2)
    print(f"✅ Saved cookies -> {os.path.abspath(cookies_path)} and localStorage -> {os.path.abspath(lstorage_path)}")


def load_cookies_and_storage(driver, cookies_path=COOKIES_PATH, lstorage_path=LSTORAGE_PATH):
    driver.get("https://www.tiktok.com/")
    human_sleep(1.0, 1.6)

    if os.path.exists(lstorage_path):
        with open(lstorage_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data.items():
            try:
                driver.execute_script("localStorage.setItem(arguments[0], arguments[1]);", k, v)
            except JavascriptException:
                pass

    if os.path.exists(cookies_path):
        with open(cookies_path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        for c in cookies:
            c = {k: v for k, v in c.items() if k in {"name", "value", "domain", "path", "expiry", "httpOnly", "secure", "sameSite"}}
            try:
                driver.add_cookie(c)
            except WebDriverException:
                try:
                    c.pop("domain", None)
                    driver.add_cookie(c)
                except WebDriverException:
                    pass

    driver.get("https://www.tiktok.com/foryou")
    human_sleep(1.2, 2.0)


def dismiss_banners(driver):
    try:
        driver.execute_script(
            """
          const btns=[...document.querySelectorAll('button')];
          const targets=['accept all','accept','ok','agree','allow'];
          for (const b of btns){
            const t=(b.textContent||'').trim().toLowerCase();
            if (targets.some(x=>t.includes(x))) { try{ b.click(); }catch(e){} }
          }
        """
        )
    except JavascriptException:
        pass


# -------------------------- JSON helpers --------------------------
def _read_sigi_json(driver):
    """Return TikTok's JSON from <script id="SIGI_STATE"> as dict, or None."""
    try:
        js = "var el=document.querySelector('#SIGI_STATE'); return el?el.textContent:'';"
        txt = driver.execute_script(js) or ""
        if not txt.strip():
            return None
        return json.loads(txt)
    except Exception:
        return None


def _pick_visible_video_id(driver):
    """Best-effort: aweme id of the currently visible video."""
    try:
        js = r"""
        (() => {
          const vids = Array.from(document.querySelectorAll('video'));
          if (!vids.length) return "";
          const area = v => { const r=v.getBoundingClientRect();
            const vw=Math.max(document.documentElement.clientWidth,window.innerWidth||0);
            const vh=Math.max(document.documentElement.clientHeight,window.innerHeight||0);
            const ix=Math.max(0,Math.min(r.right,vw)-Math.max(r.left,0));
            const iy=Math.max(0,Math.min(r.bottom,vh)-Math.max(r.top,0));
            return ix*iy; };
          let active = document.querySelector("[data-e2e='feed-active-video']");
          if (!active) active = vids.map(v=>[v,area(v)]).sort((a,b)=>b[1]-a[1]).map(x=>x[0])[0];
          if (!active) return "";
          const item = active.closest("[data-e2e='video-feed-item']") || active.closest("article") || active.parentElement;
          if (item && item.dataset){
            if (item.dataset.awemeId) return item.dataset.awemeId;
            if (item.dataset.videoId) return item.dataset.videoId;
          }
          const a = (item && item.querySelector("a[href*='/video/']")) || document.querySelector("a[href*='/video/']");
          if (a && a.href){
            const m = a.href.match(/\/video\/(\d+)/);
            if (m) return m[1];
          }
          const m2 = (location.href||"").match(/\/video\/(\d+)/);
          return m2 ? m2[1] : "";
        })();
        """
        return driver.execute_script(js) or ""
    except Exception:
        return ""


# -------------------------- extractor --------------------------
def extract_current_video(driver):
    """
    JSON-first extractor via SIGI_STATE, with DOM fallback.

    Fills:
      video_id, post_url, video_src, duration_sec, author_handle, caption,
      like_count, comment_count, share_count, music_title, is_paused
    """

    def norm_count(s):
        if s is None:
            return ""
        s = str(s).strip().lower().replace(",", "")
        m = re.match(r"(\d+(?:\.\d+)?)([km])?$", s)
        if not m:
            return s if s.isdigit() else ""
        n = float(m.group(1))
        suf = (m.group(2) or "").lower()
        if suf == "k":
            n *= 1e3
        if suf == "m":
            n *= 1e6
        return str(int(round(n)))

    data = {
        "video_id": "",
        "post_url": "",
        "video_src": "",
        "duration_sec": "",
        "author_handle": "",
        "caption": "",
        "like_count": "",
        "comment_count": "",
        "share_count": "",
        "music_title": "",
        "is_paused": "",
    }

    # 1) SIGI_STATE JSON (stable metadata)
    state = _read_sigi_json(driver)
    vid_id = _pick_visible_video_id(driver)

    if state:
        item_module = state.get("ItemModule") or {}
        if not vid_id and item_module:
            vid_id = next(iter(item_module.keys()), "")

        if vid_id and vid_id in item_module:
            itm = item_module[vid_id] or {}
            data["video_id"] = vid_id

            author = itm.get("author") or itm.get("authorUniqueId") or ""
            if author and not author.startswith("@"):
                author = "@" + author
            data["author_handle"] = author or data["author_handle"]

            data["caption"] = itm.get("desc") or data["caption"]

            stats = itm.get("stats") or {}
            data["like_count"] = norm_count(stats.get("diggCount"))
            data["comment_count"] = norm_count(stats.get("commentCount"))
            data["share_count"] = norm_count(stats.get("shareCount"))

            music = itm.get("music") or {}
            data["music_title"] = music.get("title") or data["music_title"]

            video_obj = itm.get("video") or {}
            if video_obj.get("duration"):
                data["duration_sec"] = str(video_obj.get("duration"))

            if itm.get("author") and vid_id:
                data["post_url"] = f"https://www.tiktok.com/@{itm['author']}/video/{vid_id}"

    # 2) DOM fallback to fill gaps
    try:
        dom_js = r"""
        (() => {
          const out = {};
          function pick(root, sels){
            for (const s of sels){
              const el = root ? root.querySelector(s) : document.querySelector(s);
              if (el) return el;
            }
            return null;
          }
          const vids = Array.from(document.querySelectorAll('video'));
          if (!vids.length) return out;
          let active = document.querySelector("[data-e2e='feed-active-video']");
          const area = v => { const r=v.getBoundingClientRect(); const vw=Math.max(document.documentElement.clientWidth,window.innerWidth||0); const vh=Math.max(document.documentElement.clientHeight,window.innerHeight||0); const ix=Math.max(0,Math.min(r.right,vw)-Math.max(r.left,0)); const iy=Math.max(0,Math.min(r.bottom,vh)-Math.max(r.top,0)); return ix*iy; };
          if (!active) active = vids.map(v=>[v,area(v)]).sort((a,b)=>b[1]-a[1]).map(x=>x[0])[0];
          if (!active) return out;
          const item = active.closest("[data-e2e='video-feed-item']") || active.closest("article") || active.parentElement;

          out.video_src = active.currentSrc || active.src || "";
          out.is_paused = !!active.paused;

          const a = (item && item.querySelector("a[href*='/video/']")) || document.querySelector("a[href*='/video/']");
          out.post_url = a ? a.href : "";

          let author = (item && pick(item, ["[data-e2e='video-author-uniqueid']"])) || pick(document, ["[data-e2e='video-author-uniqueid']"]);
          if (!author) {
            author = (item && Array.from(item.querySelectorAll('a')).find(x => (x.getAttribute('href')||'').startsWith('/@'))) ||
                     Array.from(document.querySelectorAll('a')).find x => (x.getAttribute('href')||'').startsWith('/@'));
          }
          out.author_handle = author ? (author.textContent||"").trim() : "";

          let cap = (item && pick(item, ["[data-e2e='browse-video-desc']","[data-e2e='video-desc']","h1","p"])) ||
                    pick(document, ["[data-e2e='browse-video-desc']","[data-e2e='video-desc']"]);
          out.caption = cap ? (cap.textContent||"").trim() : "";

          let mus = (item && pick(item, ["[data-e2e='browse-music']","[data-e2e='music-title']","a[href*='/music/']"])) ||
                    pick(document, ["[data-e2e='browse-music']","[data-e2e='music-title']","a[href*='/music/']"]);
          out.music_title = mus ? (mus.textContent||"").trim() : "";

          out.duration_sec = Number.isFinite(active.duration) ? String(active.duration) : "";
          return out;
        })();
        """
        dom = driver.execute_script(dom_js) or {}
        for k in ["video_src", "is_paused", "post_url", "author_handle", "caption", "music_title", "duration_sec"]:
            if not data.get(k) and dom.get(k) not in (None, ""):
                data[k] = dom.get(k)
    except Exception:
        pass

    if not data["post_url"] and data["author_handle"] and data["video_id"]:
        handle = data["author_handle"]
        if not handle.startswith("@"):
            handle = "@" + handle
        data["post_url"] = f"https://www.tiktok.com/{handle}/video/{data['video_id']}"

    if data["author_handle"] and not data["author_handle"].startswith("@"):
        data["author_handle"] = "@" + data["author_handle"]

    return data


# -------------------------- scrolling --------------------------
def scroll_next(driver, attempts=5):
    """
    Scroll the TikTok feed container if present; else window/END key.
    Returns True if scroll position changed meaningfully.
    """
    # Try container scroll
    before = driver.execute_script(
        """
        const feed=document.querySelector('[data-e2e="scroll-list"]')
                 ||document.querySelector('[data-e2e="browse-feed"]');
        if (feed){
          const b=feed.scrollTop;
          feed.scrollBy(0, Math.max(600, feed.clientHeight*0.9));
          return ['container', b];
        } else {
          const b=window.pageYOffset || document.documentElement.scrollTop;
          window.scrollBy(0, 900);
          return ['window', b];
        }
        """
    )
    mode, prev = before[0], before[1]

    for _ in range(attempts):
        human_sleep(1.0, 1.8)
        after = driver.execute_script(
            """
            const feed=document.querySelector('[data-e2e="scroll-list"]')
                     ||document.querySelector('[data-e2e="browse-feed"]');
            if (feed){
              return ['container', feed.scrollTop];
            } else {
              return ['window', window.pageYOffset || document.documentElement.scrollTop];
            }
            """
        )
        now_mode, now_val = after[0], after[1]
        if now_val - prev > 40:
            return True

        # gentle fallback nudges
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.END)
        except Exception:
            pass
        try:
            driver.execute_script("window.scrollBy(0, 800);")
        except Exception:
            pass

    return False


# -------------------------- IO helpers --------------------------
def open_csv_writer(out_csv):
    ensure_parent(out_csv)
    file_obj = open(out_csv, "w", newline="", encoding="utf-8")
    fieldnames = [
        "ts_iso",
        "index",
        "video_id",
        "post_url",
        "video_src",
        "duration_sec",
        "author_handle",
        "caption",
        "like_count",
        "comment_count",
        "share_count",
        "music_title",
        "is_paused",
    ]
    writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
    writer.writeheader()
    file_obj.flush()
    return file_obj, writer, fieldnames


def do_login_and_save(driver, timeout_min=5):
    print("Opening TikTok login… Complete login **manually** in the browser window.")
    driver.get("https://www.tiktok.com/login")
    start = time.time()
    while time.time() - start < timeout_min * 60:
        if "tiktok.com/login" not in driver.current_url and driver.get_cookies():
            save_cookies_and_storage(driver)
            print("✅ Login detected & session saved.")
            return
        human_sleep(1.0, 1.8)
    raise TimeoutException("Login not detected within the time window.")


# -------------------------- main loop --------------------------
def run(mode, max_videos, out_csv, headless, start_url, delay_min, delay_max):
    driver = start_driver(headless=headless)
    csv_file = None
    try:
        if mode == "login":
            do_login_and_save(driver)
            return

        if not os.path.exists(COOKIES_PATH):
            raise RuntimeError("Run first with --mode login to save cookies.")

        abs_csv = os.path.abspath(out_csv)
        print(f"📄 Writing CSV to: {abs_csv}")
        csv_file, writer, fieldnames = open_csv_writer(abs_csv)

        load_cookies_and_storage(driver)
        human_sleep(1.0, 1.6)
        driver.get(start_url)
        human_sleep(2.0, 3.0)
        dismiss_banners(driver)

        seen = set()
        count = 0
        no_progress_strikes = 0
        MAX_STRIKES = 10  # stop if we fail to progress 10 times in a row

        while count < max_videos:
            # up to 3 attempts to let the page hydrate
            attempts = 0
            row_data = {}
            while attempts < 3:
                human_sleep(0.6, 1.2)
                row_data = extract_current_video(driver)
                if any(row_data.get(k) for k in ("video_id", "post_url", "author_handle")):
                    break
                attempts += 1

            if not any(row_data.get(k) for k in ("video_id", "post_url", "author_handle")):
                no_progress_strikes += 1
                if no_progress_strikes >= MAX_STRIKES:
                    print("⚠️  No progress after multiple attempts; stopping.")
                    break
                scroll_next(driver)
                continue

            vid = row_data.get("video_id", "")
            if vid and vid in seen:
                scroll_next(driver)
                continue
            if vid:
                seen.add(vid)

            row = {
                "ts_iso": datetime.utcnow().isoformat(),
                "index": count + 1,
                "video_id": row_data.get("video_id", ""),
                "post_url": row_data.get("post_url", ""),
                "video_src": row_data.get("video_src", ""),
                "duration_sec": row_data.get("duration_sec", ""),
                "author_handle": row_data.get("author_handle", ""),
                "caption": row_data.get("caption", ""),
                "like_count": row_data.get("like_count", ""),
                "comment_count": row_data.get("comment_count", ""),
                "share_count": row_data.get("share_count", ""),
                "music_title": row_data.get("music_title", ""),
                "is_paused": row_data.get("is_paused", ""),
            }

            # write & flush
            writer.writerow(row)
            csv_file.flush()
            os.fsync(csv_file.fileno())  # ensure it hits disk
            print(f"[{count+1}/{max_videos}] wrote row → id:{row['video_id']} url:{row['post_url']}")

            count += 1
            no_progress_strikes = 0  # reset since we wrote a row

            human_sleep(delay_min, delay_max)
            if not scroll_next(driver):
                no_progress_strikes += 1
                if no_progress_strikes >= MAX_STRIKES:
                    print("⚠️  Reached end or cannot scroll further; stopping.")
                    break

        save_cookies_and_storage(driver)
        print(f"\n✅ Done. Saved {count} rows to {abs_csv}")

    finally:
        try:
            if csv_file:
                csv_file.close()
        except Exception:
            pass
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="TikTok Selenium feed sampler (JSON-first + DOM fallback) with robust CSV.")
    ap.add_argument("--mode", choices=["login", "scrape"], default="scrape", help="login: save cookies; scrape: reuse cookies")
    ap.add_argument("--max_videos", type=int, default=30)
    ap.add_argument("--out_csv", type=str, default="tiktok_feed_sample.csv")
    ap.add_argument("--headless", action="store_true", help="Headless mode (works after cookies are saved).")
    ap.add_argument("--start_url", type=str, default="https://www.tiktok.com/foryou")
    ap.add_argument("--delay_min", type=float, default=1.5)
    ap.add_argument("--delay_max", type=float, default=3.0)
    args = ap.parse_args()

    if args.delay_max < args.delay_min:
        args.delay_max = args.delay_min

    run(
        mode=args.mode,
        max_videos=args.max_videos,
        out_csv=args.out_csv,
        headless=args.headless,
        start_url=args.start_url,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
    )

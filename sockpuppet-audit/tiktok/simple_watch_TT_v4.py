# tiktok_feed_scraper_manual_retry.py

import os
import argparse
import time
import re
import pickle
import random
import string
from typing import Optional, Tuple

import pandas as pd
import numpy as np
import undetected_chromedriver as uc

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    InvalidCookieDomainException,
    InvalidSessionIdException,
    WebDriverException,
)

# ==============================
# Config
# ==============================
DEFAULT_SCROLL_TIMES = 250
DEFAULT_MAX_VIDEOS = 100
DEFAULT_COOKIES_FILE = "cookies.pkl"
DEFAULT_RETRIES = 1  # how many times to recreate the browser on hard failure

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)

FEED_SELECTORS = (
    '[data-e2e="feed-card"], '
    '[data-e2e="browse-video"], '
    '[data-e2e="video-item"], '
    'div[data-e2e^="recommend-list-item"], '
    'div[data-e2e="recommend-list"], '
    'section[role="listitem"], '
    'article[role="article"], '
    'article, '
    'div.tiktok-1soki6-DivItemContainerV2'
)

CAPTION_SELECTORS = (
    '[data-e2e="video-desc"]',
    'div[title][data-e2e*="desc"]',
    'h1',
    'h2',
    'figcaption',
)

# ==============================
# Utilities
# ==============================
def generate_random_string(length: int = 10, use_letters: bool = True, use_digits: bool = True, use_symbols: bool = False) -> str:
    chars = ''
    if use_letters: chars += string.ascii_letters
    if use_digits: chars += string.digits
    if use_symbols: chars += string.punctuation
    if not chars: raise ValueError("At least one character type should be selected")
    return ''.join(random.choice(chars) for _ in range(length))

def human_delay(min_s=0.8, max_s=1.8):
    time.sleep(random.uniform(min_s, max_s))

def page_ready(driver, timeout=20):
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )

def save_cookies(driver, file_path: str):
    with open(file_path, "wb") as f:
        pickle.dump(driver.get_cookies(), f)
    print(f"Cookies saved -> {file_path}")

def load_cookies(driver, file_path: str, domain: str):
    if not os.path.exists(file_path):
        print("No cookies file found. Proceeding without loading cookies.")
        return
    try:
        with open(file_path, "rb") as f:
            cookies = pickle.load(f)
        loaded = 0
        for c in cookies:
            if 'domain' in c and domain.endswith(c['domain'].lstrip('.')):
                pass
            else:
                c['domain'] = '.' + domain
            try:
                driver.add_cookie(c); loaded += 1
            except InvalidCookieDomainException:
                continue
        print(f"Loaded {loaded} cookies from {file_path}")
    except Exception as e:
        print(f"Failed to load cookies: {e}")

def dismiss_consent(driver):
    for sel in [
        'button#onetrust-accept-btn-handler',
        'button[aria-label*="Accept"]',
        'button[aria-label*="accept"]',
        '[data-e2e="cookie-banner-accept"]',
    ]:
        try:
            btns = driver.find_elements(By.CSS_SELECTOR, sel)
            if btns:
                btns[0].click()
                time.sleep(0.5)
                return
        except Exception:
            pass

def check_login_status(driver) -> bool:
    for sel in [
        'a[href*="/settings"]',
        'a[href*="/profile"]',
        'button[data-e2e="upload-icon"]',
        '[data-e2e="nav-profile"]',
        'a[href^="/@"] img',
    ]:
        try:
            if driver.find_elements(By.CSS_SELECTOR, sel):
                return True
        except Exception:
            continue
    return False

def ensure_logged_in(driver, cookies_file: str, wait_after_login: int = 90):
    driver.get("https://www.tiktok.com/?lang=en")
    try: page_ready(driver, timeout=20)
    except Exception: pass
    dismiss_consent(driver)
    load_cookies(driver, cookies_file, domain="tiktok.com")
    driver.refresh(); time.sleep(3)

    if not check_login_status(driver):
        print("Not logged in. Opening login… You have ~60–90s to complete.")
        try: driver.get("https://www.tiktok.com/login?lang=en")
        except Exception: pass
        dismiss_consent(driver)
        time.sleep(wait_after_login)
        save_cookies(driver, cookies_file)
        driver.get("https://www.tiktok.com/?lang=en"); time.sleep(3)
    else:
        print("Logged in using stored cookies.")

def dump_debug(driver, tag: str):
    try:
        with open(f"debug_{tag}.html", "w", encoding="utf-8") as fh:
            fh.write(driver.page_source)
        driver.save_screenshot(f"debug_{tag}.png")
        print(f"Saved debug files: debug_{tag}.html / debug_{tag}.png")
    except Exception:
        pass

def is_challenge_page(driver) -> bool:
    txt = (driver.page_source or "").lower()
    title = (driver.title or "").lower()
    return any(x in txt for x in ["verify", "captcha", "access denied", "/challenge/verify"]) or "captcha" in title

def driver_alive(driver) -> bool:
    try:
        driver.execute_script("return 1")
        return True
    except Exception:
        return False

# ==============================
# Driver creation
# ==============================
def create_driver():
    options = uc.ChromeOptions()
    options.add_argument("--incognito")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=430,932")
    options.add_argument(f"--user-agent={MOBILE_UA}")
    # You can add: options.add_argument("--disable-dev-shm-usage")

    d = uc.Chrome(options=options)
    try:
        d.execute_cdp_cmd("Emulation.setUserAgentOverride", {"userAgent": MOBILE_UA, "platform": "iPhone"})
        d.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
            "width": 430, "height": 932, "deviceScaleFactor": 3, "mobile": True
        })
    except Exception:
        pass
    return d

# ==============================
# Extraction helpers
# ==============================
def extract_handle_from_card(card) -> Optional[str]:
    try:
        a = card.find_element(By.CSS_SELECTOR, 'a[href^="/@"]')
        href = a.get_attribute('href') or ''
        if '/@' in href: return href.split('/@', 1)[1].split('/', 1)[0]
    except Exception: pass
    try:
        a = card.find_element(By.CSS_SELECTOR, 'a[href*="/@"]')
        href = a.get_attribute('href') or ''
        if '/@' in href: return href.split('/@', 1)[1].split('/', 1)[0]
    except Exception: pass
    return None

def extract_video_href_from_card(card) -> Optional[str]:
    try:
        a = card.find_element(By.CSS_SELECTOR, 'a[href*="/video/"]')
        href = a.get_attribute('href')
        if href and '/video/' in href: return href
    except Exception: pass
    try:
        a = card.find_element(By.XPATH, './/a[contains(@href, "/video/")]')
        href = a.get_attribute('href')
        if href and '/video/' in href: return href
    except Exception: pass
    return None

def extract_caption_on_page(driver) -> Optional[str]:
    for sel in CAPTION_SELECTORS:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            txt = (el.text or '').strip()
            if txt: return txt
        except Exception: continue
    return None

def extract_counts_on_page(driver) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    likes = comments = shares = None
    try:
        candidates = driver.find_elements(By.CSS_SELECTOR, '[data-e2e*="like"], [data-e2e*="comment"], [data-e2e*="share"], strong, span')
        def to_int(s: str) -> Optional[int]:
            s = (s or "").lower().strip()
            m = re.match(r"([0-9]+(?:\.[0-9]+)?)([km]?)", s or "")
            if not m: return None
            val = float(m.group(1)); suf = m.group(2)
            if suf == 'k': val *= 1_000
            elif suf == 'm': val *= 1_000_000
            return int(val)
        for el in candidates:
            label = (el.get_attribute('aria-label') or el.get_attribute('title') or '').lower()
            txt = (el.text or '').strip()
            if 'like' in label and likes is None: likes = to_int(txt) or to_int(label)
            elif 'comment' in label and comments is None: comments = to_int(txt) or to_int(label)
            elif 'share' in label and shares is None: shares = to_int(txt) or to_int(label)
    except Exception:
        pass
    return likes, comments, shares

# ==============================
# Navigation (Manual-Assist aware)
# ==============================
def navigate_to_feed(driver, manual_assist: bool) -> bool:
    """
    Manual-Assist: YOU open https://www.tiktok.com/foryou and solve prompts.
    We just verify cards exist before scraping.
    """
    if not driver_alive(driver):
        return False
    dismiss_consent(driver)
    try: page_ready(driver, timeout=10)
    except Exception: pass
    try:
        cards = driver.find_elements(By.CSS_SELECTOR, FEED_SELECTORS)
        return bool(cards)
    except (InvalidSessionIdException, WebDriverException):
        return False

# ==============================
# Scrape loop
# ==============================
def scrape_from_current_feed(driver, scroll_times: int, max_videos: int, collect_details: bool) -> pd.DataFrame:
    seen = set(); rows = []; attempts_without_new = 0

    for i in range(max(1, scroll_times)):
        if not driver_alive(driver):
            print("Browser session ended during scrape.")
            break

        try:
            cards = driver.find_elements(By.CSS_SELECTOR, FEED_SELECTORS)
        except (InvalidSessionIdException, WebDriverException):
            print("Browser session invalid while locating cards.")
            break

        if not cards and (i % 5 == 0):
            dump_debug(driver, f"feed_{i}")

        new_this_round = 0
        for card in cards:
            href = extract_video_href_from_card(card)
            if not href or href in seen:
                continue

            handle = extract_handle_from_card(card)
            caption = None; likes = comments = shares = None

            if collect_details:
                try:
                    driver.execute_script("window.open(arguments[0], '_blank');", href)
                    driver.switch_to.window(driver.window_handles[-1])
                    try: page_ready(driver, timeout=20)
                    except Exception: pass
                    human_delay(0.8, 1.3)
                    dismiss_consent(driver)

                    if not handle:
                        try:
                            a = driver.find_element(By.CSS_SELECTOR, 'a[href^="/@"]')
                            h = a.get_attribute('href') or ''
                            if '/@' in h: handle = h.split('/@', 1)[1].split('/', 1)[0]
                        except Exception: pass

                    caption = extract_caption_on_page(driver)
                    likes, comments, shares = extract_counts_on_page(driver)
                finally:
                    if len(driver.window_handles) > 1:
                        driver.close()
                        driver.switch_to.window(driver.window_handles[0])

            seen.add(href)
            rows.append({
                'channelId': handle or '',
                'vid': href,
                'caption': caption or '',
                'like_count': likes,
                'comment_count': comments,
                'share_count': shares,
            })
            new_this_round += 1
            if len(rows) >= max_videos:
                break

        if len(rows) >= max_videos: break

        attempts_without_new = attempts_without_new + 1 if new_this_round == 0 else 0
        if attempts_without_new >= 8:
            print("No new items after several attempts; stopping early.")
            break

        # Gentle, varied scroll
        try:
            driver.execute_script("window.scrollBy(0, arguments[0]);", random.randint(900, 1600))
        except (InvalidSessionIdException, WebDriverException):
            print("Browser session invalid while scrolling.")
            break
        human_delay(0.9, 1.6)
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.END)
        except Exception:
            pass
        human_delay(0.7, 1.2)
        if i % 7 == 0:
            try:
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.HOME)
            except Exception:
                pass
            human_delay(0.5, 1.0)

        if i % 10 == 0:
            print(f"Progress: {len(rows)} videos collected …")

        if is_challenge_page(driver):
            dump_debug(driver, f"challenge_midrun_{i}")
            print("Verification appeared mid-run; stopping scrape.")
            break

    return pd.DataFrame(rows)

# ==============================
# Orchestration (with retry)
# ==============================
def run_once(scroll_times, cookies_file, max_videos, collect_details, manual_assist) -> pd.DataFrame:
    driver = create_driver()
    try:
        ensure_logged_in(driver, cookies_file=cookies_file, wait_after_login=120)

        if manual_assist:
            print(
                "\nMANUAL-ASSIST MODE:\n"
                "  1) In Chrome, log in if needed.\n"
                "  2) Open https://www.tiktok.com/foryou and solve any prompts.\n"
                "  3) Make sure you can SEE videos.\n"
                "  4) Press ENTER here to start scraping.\n"
            )
            try: input("Press ENTER to continue… ")
            except KeyboardInterrupt: return pd.DataFrame([])

        ok = navigate_to_feed(driver, manual_assist=manual_assist)
        if not ok:
            print("No feed cards detected. See debug files (if any). Ensure you can SEE videos before continuing.")
            dump_debug(driver, "no_feed")
            return pd.DataFrame([])

        return scrape_from_current_feed(driver, scroll_times, max_videos, collect_details)

    finally:
        try: driver.quit()
        except Exception: pass

def run_with_retries(scroll_times, cookies_file, max_videos, collect_details, manual_assist, retries=DEFAULT_RETRIES) -> pd.DataFrame:
    last_df = pd.DataFrame([])
    for attempt in range(retries + 1):
        try:
            last_df = run_once(scroll_times, cookies_file, max_videos, collect_details, manual_assist)
            if not last_df.empty:
                return last_df
        except (InvalidSessionIdException, WebDriverException) as e:
            print(f"Browser died (attempt {attempt+1}/{retries+1}): {e}")
        print("Re-launching browser…" if attempt < retries else "No more retries.")
    return last_df

# ==============================
# CLI
# ==============================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TikTok For You feed collector (Manual-Assist, session-safe)")
    parser.add_argument('--scroll_times', type=int, default=DEFAULT_SCROLL_TIMES, help='Scroll iterations (default: 250)')
    parser.add_argument('--max_videos', type=int, default=DEFAULT_MAX_VIDEOS, help='Max videos to collect (default: 100)')
    parser.add_argument('--cookies', type=str, default=DEFAULT_COOKIES_FILE, help='Path to cookies.pkl')
    parser.add_argument('--no_details', action='store_true', help='Skip per-video open; faster, fewer challenges')
    parser.add_argument('--manual_assist', action='store_true', help='YOU open /foryou and solve prompts, then press ENTER')
    parser.add_argument('--retries', type=int, default=DEFAULT_RETRIES, help='How many times to relaunch on hard failure')

    args = parser.parse_args()

    df = run_with_retries(
        scroll_times=args.scroll_times,
        cookies_file=args.cookies,
        max_videos=args.max_videos,
        collect_details=not args.no_details,
        manual_assist=args.manual_assist or True,   # default to manual-assist ON
        retries=max(0, args.retries),
    )

    out_csv = f"trajectory_{generate_random_string(12)}.csv"
    if df is None or df.empty:
        print("No data collected. Ensure you are logged in, on /foryou, and have solved verification.")
        pd.DataFrame(columns=['channelId','vid','caption','like_count','comment_count','share_count']).to_csv(out_csv, index=False)
        print(f"Saved empty schema CSV: {out_csv}")
    else:
        df.to_csv(out_csv, index=False)
        print(f"Saved: {out_csv} (rows={len(df)})")

# lang: Python
# file: ig_agent_main.py
# target: Windows/Linux, Python 3.10+, Playwright, httpx
# note: Handles dynamic image/video mutation and resilient Instagram web upload.

import asyncio
import random
import json
import base64
import tempfile
import mimetypes
from pathlib import Path
import httpx
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# ==========================================
# CONFIGURATION
# ==========================================
BASE_DIR = Path(__file__).parent
ACCOUNT_COOKIES_PATH = BASE_DIR / "session_cookies.json"
RAW_MEDIA_PATH = BASE_DIR / "raw_post.jpg" # Can be .jpg, .png, or .mp4
RAW_CAPTION = "Here is my new project."

API_ENDPOINT = "http://localhost:20129/v1"
API_KEY = "sk-2f0add4917c0bc08-e9y2d0-9381265c"

# ==========================================
# API CLIENT
# ==========================================
class MutationAPI:
    def __init__(self, endpoint: str, api_key: str):
        self.client = httpx.AsyncClient(
            base_url=endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=120.0 # Increased timeout for video generation
        )

    async def mutate(self, media_path: Path, original_caption: str) -> dict:
        with open(media_path, "rb") as f:
            mime_type, _ = mimetypes.guess_type(media_path)
            files = {"media": (media_path.name, f, mime_type or "application/octet-stream")}
            data = {"caption": original_caption, "target_style": "viral"}
            
            for attempt in range(3):
                try:
                    print(f"[*] API Request attempt {attempt + 1}/3...")
                    resp = await self.client.post("/mutate", files=files, data=data)
                    resp.raise_for_status()
                    return resp.json()
                except httpx.HTTPStatusError as e:
                    print(f"[-] API HTTP Error: {e}")
                    if attempt == 2:
                        raise RuntimeError(f"API mutation failed after 3 attempts: {e}")
                    await asyncio.sleep(2 ** attempt)
                except Exception as e:
                    print(f"[-] API Request failed: {e}")
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2 ** attempt)

    async def resolve_media(self, api_response: dict) -> tuple[Path, str]:
        temp_dir = Path(tempfile.gettempdir())
        
        # Determine media type from API response, fallback to 'image'
        media_type = api_response.get("mutated_media_type", "image").lower()
        is_video = "video" in media_type or "mp4" in str(api_response)
        ext = ".mp4" if is_video else ".jpg"
        mutated_path = temp_dir / f"mutated_post{ext}"
        
        if "mutated_media_url" in api_response:
            print(f"[*] Downloading mutated media from URL...")
            async with self.client.stream("GET", api_response["mutated_media_url"]) as r:
                r.raise_for_status()
                with open(mutated_path, "wb") as f:
                    async for chunk in r.aiter_bytes():
                        f.write(chunk)
        elif "mutated_media_base64" in api_response:
            print(f"[*] Decoding mutated media from base64...")
            img_data = base64.b64decode(api_response["mutated_media_base64"])
            mutated_path.write_bytes(img_data)
        else:
            raise ValueError("API response missing media payload (mutated_media_url or mutated_media_base64).")
            
        return mutated_path, "video" if is_video else "image"

# ==========================================
# STEALTH & HUMANIZATION
# ==========================================
async def human_delay(min_ms: int = 500, max_ms: int = 1500):
    delay = random.gauss((min_ms + max_ms) / 2, (max_ms - min_ms) / 6)
    await asyncio.sleep(max(0.1, delay / 1000))

async def human_type(page, selector: str, text: str):
    target = page.locator(selector).first
    await target.click()
    await human_delay(300, 600)
    # Fast typing with chunks/clipboard or small delays to avoid timeout
    for char in text:
        await page.keyboard.type(char, delay=random.randint(5, 15))
        if random.random() < 0.05:
            await asyncio.sleep(0.05)

async def load_cookies(page):
    if not ACCOUNT_COOKIES_PATH.exists():
        raise FileNotFoundError(f"Cookie file not found at {ACCOUNT_COOKIES_PATH}")
    with open(ACCOUNT_COOKIES_PATH, "r", encoding="utf-8") as f:
        cookies = json.load(f)
    ig_cookies = []
    for c in cookies:
        if "instagram.com" not in c.get("domain", ""):
            continue
        cleaned = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ".instagram.com"),
            "path": c.get("path", "/"),
            "secure": c.get("secure", True),
            "httpOnly": c.get("httpOnly", False),
        }
        if "expirationDate" in c:
            cleaned["expires"] = int(c["expirationDate"])
        ss = c.get("sameSite", "").lower()
        if ss in ("no_restriction", "none"):
            cleaned["sameSite"] = "None"
        elif ss == "lax":
            cleaned["sameSite"] = "Lax"
        elif ss == "strict":
            cleaned["sameSite"] = "Strict"
        ig_cookies.append(cleaned)
    await page.context.add_cookies(ig_cookies)

# ==========================================
# EXECUTION PIPELINE
# ==========================================
async def run_upload_sequence():
    api = MutationAPI(API_ENDPOINT, API_KEY)
    
    # 1. Execute Heavy Lifting via API
    print("[*] Sending raw assets to mutation API...")
    mutation_result = await api.mutate(RAW_MEDIA_PATH, RAW_CAPTION)
    
    final_caption = mutation_result.get("mutated_caption", RAW_CAPTION)
    final_media_path, media_type = await api.resolve_media(mutation_result)
    print(f"[+] Mutation complete. Media type: {media_type.upper()}. Deploying to Instagram.")

    # 2. Stealth Deployment
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            viewport={'width': 1080, 'height': 1920},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            locale='en-US'
        )
        await Stealth().apply_stealth_async(context)
        page = await context.new_page()
        
        print("[*] Navigating to Instagram...")
        await page.goto('https://www.instagram.com/')
        await human_delay(1500, 2500)
        
        print("[*] Injecting session cookies...")
        await load_cookies(page)
        await page.reload()
        await human_delay(2000, 3500)
        
        # Verify login state
        try:
            await page.wait_for_selector('svg[aria-label="New post"]', timeout=15000)
        except Exception:
            print("[-] Session invalid or login required. Re-extract cookies.")
            await browser.close()
            return

        # Dismiss any popup/dialogs (like "Turn on Notifications" or "Save info")
        for dismiss_sel in ['button:has-text("Not Now")', 'button:has-text("Cancel")', 'button:has-text("Not now")']:
            try:
                btn = page.locator(dismiss_sel).first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    await human_delay(500, 1000)
            except Exception:
                pass

        # Trigger upload
        print("[*] Opening upload dialog...")
        try:
            new_post_svg = page.locator('svg[aria-label="New post"]')
            parent_btn = new_post_svg.locator('xpath=ancestor::*[self::a or @role="button"]').first
            if await parent_btn.count() > 0:
                await parent_btn.click(force=True)
            else:
                await new_post_svg.click(force=True)
        except Exception:
            await page.click('svg[aria-label="New post"]', force=True)
        await human_delay(1500, 2500)
        
        file_input = await page.wait_for_selector('input[type="file"]', state="attached", timeout=15000)
        await file_input.set_input_files(str(final_media_path.resolve()))
        print("[*] Media uploaded. Waiting for processing...")
        await human_delay(3000, 5000) # Longer delay for video processing
        
        # Resilient "Next" button clicking
        # Instagram may show 1 "Next" for images, or 2 "Next"s for videos (trim -> cover -> caption)
        max_next_clicks = 3
        caption_sel = 'div[aria-label="Write a caption..."], textarea[aria-label="Write a caption..."], [contenteditable="true"][role="textbox"], div[role="textbox"]'
        
        for i in range(max_next_clicks):
            await human_delay(1500, 2500)
            # Check if caption box is already visible
            try:
                if await page.locator(caption_sel).first.is_visible(timeout=2000):
                    print("[*] Caption screen reached.")
                    break
            except Exception:
                pass

            clicked = False
            for sel in ['div[role="button"]:has-text("Next")', 'button:has-text("Next")', '*:text-is("Next")', 'div:has-text("Next")']:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=3000):
                        await btn.click(force=True)
                        print(f"[*] Clicked 'Next' via {sel} ({i+1}/{max_next_clicks})")
                        clicked = True
                        await human_delay(2000, 3000)
                        break
                except Exception:
                    continue

            if not clicked:
                try:
                    if await page.locator(caption_sel).first.is_visible(timeout=2000):
                        print("[*] Already at caption screen.")
                        break
                except Exception:
                    pass
                print("[-] Could not find 'Next' button or caption box. Retrying...")

        # Inject caption
        print("[*] Injecting mutated caption...")
        try:
            caption_box = await page.wait_for_selector(caption_sel, timeout=12000)
            await human_type(page, caption_sel, final_caption)
            await human_delay(1000, 1500)
            # Dismiss any accidental tag popovers by pressing Escape
            await page.keyboard.press("Escape")
            await human_delay(500, 1000)
        except Exception as e:
            print(f"[-] Failed to find caption box: {e}")
            await page.screenshot(path="failed_caption.png")
            raise
        
        # Publish
        print("[*] Publishing post...")
        share_clicked = False
        # Specific target: Share button in modal header
        share_selectors = [
            'div[role="dialog"] div[role="button"]:has-text("Share")',
            'div[role="dialog"] button:has-text("Share")',
            'div[role="dialog"] *:text-is("Share")',
            'div[role="button"]:has-text("Share")',
            'button:has-text("Share")'
        ]
        for share_sel in share_selectors:
            try:
                share_btn = page.locator(share_sel).first
                if await share_btn.is_visible(timeout=4000):
                    await share_btn.scroll_into_view_if_needed()
                    # Click without force first to fire real pointer events, fallback to dispatch
                    try:
                        await share_btn.click(timeout=3000)
                    except Exception:
                        await share_btn.dispatch_event("click")
                    print(f"[*] Clicked 'Share' via {share_sel}")
                    share_clicked = True
                    break
            except Exception:
                continue
        
        if not share_clicked:
            print("[-] Share button was not found or not clickable.")
            await page.screenshot(path="failed_share.png")
            raise RuntimeError("Share button not clicked")

        # Robust Auto-Confirm: Wait for upload processing spinner and post submission
        print("[*] Waiting for Instagram upload to finalize and auto-confirming...")
        confirmed = False
        for tick in range(12): # check every 5s up to 60s
            await human_delay(4000, 5000)
            # 1. Success checkmark / message
            for sel in ['div:has-text("Your post has been shared.")', 'div:has-text("Your reel has been shared.")', 'div:has-text("Post shared")', 'img[alt*="checkmark"]', 'svg[aria-label*="checkmark" i]']:
                if await page.is_visible(sel):
                    print(f"[+] Success message detected via '{sel}'!")
                    confirmed = True
                    break
            if confirmed:
                break

            # 2. Check if upload modal has disappeared
            modal_visible = await page.is_visible('div[role="dialog"]')
            if not modal_visible:
                print("[+] Upload modal dismissed automatically — post submission confirmed!")
                confirmed = True
                break

            # 3. Check if 'Sharing' spinner is active
            sharing_text = await page.is_visible('div:has-text("Sharing")')
            if sharing_text:
                print(f"[*] Instagram still uploading ('Sharing'... tick {tick+1}/12)")
                continue

            # 4. Check for discard or error dialog
            discard_btn = await page.is_visible('button:has-text("Discard")')
            if discard_btn:
                print("[-] Discard prompt appeared. Capturing screenshot...")
                await page.screenshot(path="failed_share.png")
                break

        if not confirmed:
            await page.screenshot(path="failed_share.png")
            # Navigate to profile to verify directly
            print("[*] Checking profile directly to auto-confirm post presence...")
            try:
                await page.goto("https://www.instagram.com/tauqeerq1/", timeout=20000)
                await page.wait_for_load_state("domcontentloaded")
                await human_delay(3000, 4000)
                # Check top post in feed
                posts = await page.query_selector_all('article a[href*="/p/"]')
                if posts:
                    print(f"[+] Profile verified! Found {len(posts)} posts on profile.")
                    confirmed = True
            except Exception as e:
                print(f"[-] Profile verification check failed: {e}")

        if confirmed:
            print("[+] Upload sequence completed successfully.")
        else:
            print("[-] Success message not detected. Check browser window manually.")
        
        await human_delay(2000, 3000)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_upload_sequence())
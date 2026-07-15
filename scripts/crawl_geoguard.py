"""
Crawl GeoGuard Pro demo app — log in as admin + guard, screenshot every page.
Saves to D:\Virtual Patrolling\scripts\geoguard_screenshots\
"""
import os, time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

import sys
sys.stdout.reconfigure(encoding='utf-8')
OUT = Path(r"D:\Virtual Patrolling\scripts\geoguard_screenshots")
OUT.mkdir(exist_ok=True)

BASE_URL   = "https://geo-guard-pro.com"
ADMIN_EMAIL    = "demo@demo.com"
ADMIN_PASSWORD = "demo123"
GUARD_EMAIL    = "guard2@gmail.com"
GUARD_PASSWORD = "guard2123"

W, H = 1440, 900

def shot(page, name):
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    print(f"  ✓ {name}.png")

def wait(page, ms=1500):
    page.wait_for_timeout(ms)

def dismiss_popups(page):
    """Close any cookie banners or modals."""
    for sel in ["button:has-text('Accept')", "button:has-text('Close')",
                "button:has-text('Got it')", "[aria-label='Close']"]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1000):
                btn.click()
        except Exception:
            pass

def crawl_admin(browser):
    print("\n=== ADMIN SESSION ===")
    ctx = browser.new_context(viewport={"width": W, "height": H})
    page = ctx.new_page()

    # Login
    page.goto(f"{BASE_URL}/login", wait_until="networkidle")
    wait(page)
    shot(page, "00_login")

    page.fill("input[type='email'], input[name='email'], #email", ADMIN_EMAIL)
    page.fill("input[type='password'], input[name='password'], #password", ADMIN_PASSWORD)
    shot(page, "01_login_filled")
    page.click("button[type='submit'], button:has-text('Login'), button:has-text('Sign in')")
    # Wait for URL to change away from /login
    try:
        page.wait_for_url(lambda url: "login" not in url, timeout=20000)
    except Exception:
        pass
    wait(page, 3000)
    dismiss_popups(page)
    shot(page, "02_dashboard")

    # Collect all nav links visible after login
    nav_links = page.locator("nav a, aside a, [role='navigation'] a, .sidebar a, .menu a").all()
    visited = set()
    nav_items = []
    for link in nav_links:
        try:
            href = link.get_attribute("href") or ""
            text = (link.inner_text() or "").strip()
            if href and href not in visited and not href.startswith("http") and len(text) > 0:
                visited.add(href)
                nav_items.append((text, href))
        except Exception:
            pass

    print(f"  Found {len(nav_items)} nav links")

    # Visit each nav page
    for idx, (text, href) in enumerate(nav_items, start=3):
        try:
            url = BASE_URL + href if href.startswith("/") else href
            print(f"  → {text} ({url})")
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            wait(page, 1500)
            dismiss_popups(page)
            slug = text.lower().replace(" ", "_").replace("/", "_")[:30]
            shot(page, f"{idx:02d}_admin_{slug}")

            # Look for sub-tabs on this page
            tabs = page.locator("[role='tab'], .tab, .nav-tab").all()
            for tidx, tab in enumerate(tabs):
                try:
                    tab_text = (tab.inner_text() or "").strip()
                    if tab_text:
                        tab.click()
                        wait(page, 1000)
                        shot(page, f"{idx:02d}_admin_{slug}_tab{tidx}_{tab_text[:15]}")
                except Exception:
                    pass

        except PWTimeout:
            print(f"    ⚠ timeout on {href}")
        except Exception as e:
            print(f"    ⚠ error on {href}: {e}")

    ctx.close()


def crawl_guard(browser):
    print("\n=== GUARD SESSION ===")
    # Use mobile viewport for guard app
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844},
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
    )
    page = ctx.new_page()

    page.goto(f"{BASE_URL}/login", wait_until="networkidle")
    wait(page)

    try:
        page.fill("input[type='email'], input[name='email'], #email", GUARD_EMAIL)
        page.fill("input[type='password'], input[name='password'], #password", GUARD_PASSWORD)
        page.click("button[type='submit'], button:has-text('Login'), button:has-text('Sign in')")
        try:
            page.wait_for_url(lambda url: "login" not in url, timeout=20000)
        except Exception:
            pass
        wait(page, 3000)
        dismiss_popups(page)
        shot(page, "G01_guard_dashboard")

        # Collect guard nav links
        nav_links = page.locator("nav a, aside a, [role='navigation'] a, .sidebar a, .menu a, .bottom-nav a").all()
        visited = set()
        nav_items = []
        for link in nav_links:
            try:
                href = link.get_attribute("href") or ""
                text = (link.inner_text() or "").strip()
                if href and href not in visited and not href.startswith("http") and len(text) > 0:
                    visited.add(href)
                    nav_items.append((text, href))
            except Exception:
                pass

        for idx, (text, href) in enumerate(nav_items, start=2):
            try:
                url = BASE_URL + href if href.startswith("/") else href
                print(f"  → [Guard] {text}")
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                wait(page, 1500)
                dismiss_popups(page)
                slug = text.lower().replace(" ", "_").replace("/", "_")[:30]
                shot(page, f"G{idx:02d}_guard_{slug}")
            except Exception as e:
                print(f"    ⚠ {e}")

    except Exception as e:
        print(f"  ⚠ Guard login failed: {e}")
        shot(page, "G00_guard_login_error")

    ctx.close()


def main():
    print(f"Saving screenshots to: {OUT}")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            crawl_admin(browser)
            crawl_guard(browser)
        finally:
            browser.close()

    files = list(OUT.glob("*.png"))
    print(f"\n✅ Done — {len(files)} screenshots saved to {OUT}")


if __name__ == "__main__":
    main()

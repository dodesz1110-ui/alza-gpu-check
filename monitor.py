import json
import os
import hashlib
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

DATA_FILE = "site/data.json"

URL = "https://www.alza.hu/hasznalt-videokartyak-gpu-outlet/18842862.htm"

def product_id(url, name):
    return hashlib.sha256((url + "|" + name).encode()).hexdigest()[:20]

def main():
    data = {
        "products": [],
        "seen": [],
        "initialized": False,
        "last_check": None,
        "error": None
    }

    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass

    products = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        page = browser.new_page(
            viewport={"width": 1440, "height": 900},
            locale="hu-HU"
        )

        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        cards = page.locator("div.browsingitem")

        for i in range(cards.count()):
            card = cards.nth(i)

            try:
                text = card.inner_text()
                low = text.lower()

                if "rtx 5080" not in low and "rtx 5070 ti" not in low:
                    continue

                if "felbontott" not in low and "újszerű" not in low and "használt" not in low:
                    continue

                link = card.locator("a").first.get_attribute("href")
                if not link:
                    continue

                if link.startswith("/"):
                    link = "https://www.alza.hu" + link

                name = card.locator("a").first.inner_text().strip()

                price = None
                import re
                m = re.search(r"([\d .]+)\s*Ft", text)
                if m:
                    price = int(re.sub(r"\D", "", m.group(1)))

                pid = product_id(link, name)

                products.append({
                    "id": pid,
                    "category": "RTX 5080" if "rtx 5080" in low else "RTX 5070 Ti",
                    "name": name,
                    "price": price,
                    "stock": None,
                    "url": link,
                    "checked_at": datetime.now(timezone.utc).isoformat()
                })

            except Exception:
                continue

        browser.close()

    unique = {p["id"]: p for p in products}
    products = list(unique.values())

    previous_seen = set(data.get("seen", []))
    current_ids = {p["id"] for p in products}

    if not data.get("initialized"):
        data["seen"] = sorted(current_ids)
        data["initialized"] = True
    else:
        new_items = [
            p for p in products
            if p["id"] not in previous_seen
        ]

        for p in new_items:
            token = os.getenv("GITHUB_TOKEN")
            repo = os.getenv("GITHUB_REPOSITORY")

            if token and repo:
                import requests

                price = (
                    f'{p["price"]:,}'.replace(",", " ") + " Ft"
                    if p["price"]
                    else "ár nem olvasható"
                )

                requests.post(
                    f"https://api.github.com/repos/{repo}/issues",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json"
                    },
                    json={
                        "title": f"🟢 Új Alza Outlet: {p['name']}",
                        "body": (
                            f"Új {p['category']} találat!\n\n"
                            f"Ár: {price}\n\n"
                            f"Közvetlen link: {p['url']}"
                        )
                    },
                    timeout=20
                )

        data["seen"] = sorted(previous_seen | current_ids)

    data["products"] = products
    data["last_check"] = datetime.now(timezone.utc).isoformat()
    data["error"] = None

    os.makedirs("site", exist_ok=True)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()

import json
import os
import re
import hashlib
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from playwright.sync_api import sync_playwright


DATA_FILE = "site/data.json"

ALZA_URL = "https://m.alza.hu/gaming/videokartyak/vasar-hasznalt-termekek/u38842862.htm"

HEADERS = {
    "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.8"
}


def product_id(url, name):
    return hashlib.sha256(
        (url + "|" + name).encode("utf-8")
    ).hexdigest()[:20]


def get_price(text):
    matches = re.findall(r"([\d .]+)\s*Ft", text or "")

    if not matches:
        return None

    values = []

    for value in matches:
        number = int(re.sub(r"\D", "", value))
        if number > 10000:
            values.append(number)

    return min(values) if values else None


def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "products": [],
            "seen": [],
            "initialized": False,
            "last_check": None,
            "error": None
        }

    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "products": [],
            "seen": [],
            "initialized": False,
            "last_check": None,
            "error": None
        }


def save_data(data):
    os.makedirs("site", exist_ok=True)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


def send_github_issue(product):
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")

    if not token or not repo:
        return

    price = (
        f'{product["price"]:,}'.replace(",", " ") + " Ft"
        if product.get("price")
        else "ár nem olvasható"
    )

    body = (
        f"🟢 Új Alza felbontott videókártya!\n\n"
        f"**Típus:** {product['category']}\n\n"
        f"**Termék:** {product['name']}\n\n"
        f"**Ár:** {price}\n\n"
        f"**Készlet:** {product.get('stock') or 'nincs megadva'}\n\n"
        f"**Közvetlen link:** {product['url']}"
    )

    requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
        },
        json={
            "title": f"🟢 Új Alza {product['category']}: {product['name']}",
            "body": body
        },
        timeout=20
    )


def scan_alza():
    products = []

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            viewport={
                "width": 1440,
                "height": 1000
            },
            locale="hu-HU",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            )
        )

        page.goto(
            ALZA_URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(7000)

        # Görgessünk le, hogy az összes termék betöltődjön.
        for _ in range(8):
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(500)

        # Az oldalon található összes linket vizsgáljuk.
        links = page.locator("a")

        for i in range(links.count()):

            try:
                link = links.nth(i)

                name = " ".join(
                    link.inner_text().split()
                )

                if not name:
                    continue

                low = name.lower()

                if (
                    "rtx 5080" not in low
                    and "rtx 5070 ti" not in low
                ):
                    continue

                href = link.get_attribute("href")

                if not href:
                    continue

                href = urljoin(
                    "https://www.alza.hu",
                    href
                )

                # Keressük meg a termékhez tartozó
                # magasabb szintű blokkot.
                node = link
                block_text = name

                for _ in range(8):

                    try:
                        node = node.locator("..")

                        text = " ".join(
                            node.inner_text().split()
                        )

                        if len(text) > len(block_text):
                            block_text = text

                        if (
                            "felbontott" in text.lower()
                            or "újszerű" in text.lower()
                            or "használt" in text.lower()
                        ):
                            break

                    except Exception:
                        break

                block_low = block_text.lower()

                if (
                    "felbontott" not in block_low
                    and "újszerű" not in block_low
                    and "használt" not in block_low
                ):
                    continue

                # Csak valódi Alza termékoldal.
                if "alza.hu" not in href:
                    continue

                if not href.endswith(".htm"):
                    continue

                category = (
                    "RTX 5080"
                    if "rtx 5080" in low
                    else "RTX 5070 Ti"
                )

                price = get_price(block_text)

                stock = None

                stock_match = re.search(
                    r"raktáron\s*(?:>|:)?\s*(\d+)\s*db",
                    block_text,
                    re.IGNORECASE
                )

                if stock_match:
                    stock = int(
                        stock_match.group(1)
                    )

                pid = product_id(
                    href,
                    name
                )

                products.append({
                    "id": pid,
                    "category": category,
                    "name": name,
                    "price": price,
                    "stock": stock,
                    "url": href,
                    "checked_at": datetime.now(
                        timezone.utc
                    ).isoformat()
                })

            except Exception:
                continue

        browser.close()

    # Duplikációk eltávolítása.
    unique = {}

    for product in products:
        unique[product["id"]] = product

    products = list(unique.values())

    # Ár szerint rendezzük.
    products.sort(
        key=lambda x: (
            x["price"] is None,
            x["price"] or 0
        )
    )

    return products


def main():

    data = load_data()

    try:
        products = scan_alza()
        error = None

    except Exception as e:
        products = []
        error = str(e)

    previous_seen = set(
        data.get("seen", [])
    )

    current_ids = {
        product["id"]
        for product in products
    }

    # Első futás:
    # csak eltároljuk a jelenlegi kínálatot,
    # hogy ne küldjön rögtön 20 hamis riasztást.
    if not data.get("initialized"):

        data["seen"] = sorted(
            current_ids
        )

        data["initialized"] = True

    else:

        new_products = [
            product
            for product in products
            if product["id"] not in previous_seen
        ]

        for product in new_products:
            try:
                send_github_issue(product)
            except Exception:
                pass

        data["seen"] = sorted(
            previous_seen | current_ids
        )

    data["products"] = products

    data["last_check"] = datetime.now(
        timezone.utc
    ).isoformat()

    data["error"] = error

    save_data(data)


if __name__ == "__main__":
    main()

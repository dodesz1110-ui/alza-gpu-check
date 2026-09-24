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

GPU_NAMES = (
    "rtx 5080",
    "rtx 5070 ti",
)

CONDITIONS = (
    "felbontott",
    "bontott",
    "használt",
    "újszerű",
)


def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "products": [],
            "seen": [],
            "initialized": False,
            "last_check": None,
            "error": None,
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
            "error": None,
        }


def save_data(data):
    os.makedirs("site", exist_ok=True)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def product_id(url, name):
    return hashlib.sha256(
        (url + "|" + name).encode("utf-8")
    ).hexdigest()[:20]


def get_price(text):
    matches = re.findall(r"([\d .]+)\s*Ft", text)

    prices = []

    for value in matches:
        number = int(re.sub(r"\D", "", value))

        if number >= 100000:
            prices.append(number)

    return min(prices) if prices else None


def get_condition(text):
    low = text.lower()

    if "felbontott" in low:
        return "Felbontott"

    if "bontott" in low:
        return "Bontott"

    if "használt" in low:
        return "Használt"

    if "újszerű" in low:
        return "Újszerű"

    return "Ismeretlen"


def notify_github(product):
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")

    if not token or not repo:
        return

    if product.get("price"):
        price = f'{product["price"]:,}'.replace(",", " ") + " Ft"
    else:
        price = "Nincs ár"

    body = f"""## 🟢 Új Alza videókártya

**Típus:** {product["category"]}

**Állapot:** {product["condition"]}

**Termék:** {product["name"]}

**Ár:** {price}

**Készlet:** {product.get("stock") or "nincs megadva"}

**Közvetlen link:** {product["url"]}
"""

    requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={
            "title": (
                f'🟢 Új Alza {product["condition"]} '
                f'{product["category"]}: {product["name"]}'
            ),
            "body": body,
        },
        timeout=20,
    )


def scan_alza():
    products = []

    with sync_playwright() as p:

        browser = p.chromium.launch(headless=True)

        page = browser.new_page(
            viewport={
                "width": 1440,
                "height": 1000,
            },
            locale="hu-HU",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
        )

        page.goto(
            ALZA_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        page.wait_for_timeout(7000)

        for _ in range(10):
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(700)

        links = page.locator("a[href]")

        for i in range(links.count()):

            try:
                link = links.nth(i)

                href = link.get_attribute("href")

                if not href:
                    continue

                href = urljoin(
                    "https://www.alza.hu",
                    href,
                )

                if "alza.hu" not in href:
                    continue

                if not href.endswith(".htm"):
                    continue

                node = link
                best_text = ""

                for _ in range(10):

                    try:
                        node = node.locator("..")

                        text = " ".join(
                            node.inner_text().split()
                        )

                        if len(text) > len(best_text):
                            best_text = text

                        low = text.lower()

                        has_gpu = any(
                            gpu in low
                            for gpu in GPU_NAMES
                        )

                        has_condition = any(
                            condition in low
                            for condition in CONDITIONS
                        )

                        if has_gpu and has_condition:
                            break

                    except Exception:
                        break

                text = best_text
                low = text.lower()

                if not any(
                    gpu in low
                    for gpu in GPU_NAMES
                ):
                    continue

                if not any(
                    condition in low
                    for condition in CONDITIONS
                ):
                    continue

                try:
                    name = " ".join(
                        link.inner_text().split()
                    )
                except Exception:
                    name = ""

                if not name or len(name) < 5:
                    lines = [
                        line.strip()
                        for line in text.split("\n")
                        if line.strip()
                    ]

                    for line in lines:
                        line_low = line.lower()

                        if (
                            "rtx 5080" in line_low
                            or "rtx 5070 ti" in line_low
                        ):
                            name = line
                            break

                if not name:
                    continue

                category = (
                    "RTX 5080"
                    if "rtx 5080" in low
                    else "RTX 5070 Ti"
                )

                price = get_price(text)
                condition = get_condition(text)

                stock = None

                stock_match = re.search(
                    r"(?:raktáron|raktárban)"
                    r"\s*(?:>|:)?\s*(\d+)\s*db",
                    text,
                    re.IGNORECASE,
                )

                if stock_match:
                    stock = int(
                        stock_match.group(1)
                    )

                pid = product_id(
                    href,
                    name,
                )

                products.append({
                    "id": pid,
                    "category": category,
                    "condition": condition,
                    "name": name,
                    "price": price,
                    "stock": stock,
                    "url": href,
                    "checked_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                })

            except Exception:
                continue

        browser.close()

    unique = {}

    for product in products:
        unique[product["id"]] = product

    products = list(unique.values())

    products.sort(
        key=lambda x: (
            x["price"] is None,
            x["price"] or 0,
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
                notify_github(product)
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

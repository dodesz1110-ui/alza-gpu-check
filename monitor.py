import json
import os
import re
import hashlib
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from playwright.sync_api import sync_playwright


DATA_FILE = "site/data.json"

BASE_URL = (
    "https://m.alza.hu/gaming/videokartyak/"
    "vasar-hasznalt-termekek/u38842862.htm"
)

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
    matches = re.findall(
        r"([\d .]+)\s*Ft",
        text
    )

    prices = []

    for value in matches:
        number = int(
            re.sub(r"\D", "", value)
        )

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
        price = (
            f'{product["price"]:,}'
            .replace(",", " ")
            + " Ft"
        )
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
                f'{product["category"]}: '
                f'{product["name"]}'
            ),
            "body": body,
        },
        timeout=20,
    )


def scan_page(page, url):
    products = []

    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    page.wait_for_timeout(5000)

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

            # A terméklink saját szövege tartalmazza
            # a nevet, árat és állapotot.
            text = " ".join(
                link.inner_text().split()
            )

            low = text.lower()

            # Ha a link szövege túl rövid,
            # nézzük meg a szülő elemeket is.
            node = link

            for _ in range(5):

                if (
                    any(
                        gpu in low
                        for gpu in GPU_NAMES
                    )
                    and any(
                        condition in low
                        for condition in CONDITIONS
                    )
                ):
                    break

                try:
                    node = node.locator("..")

                    parent_text = " ".join(
                        node.inner_text().split()
                    )

                    if len(parent_text) > len(text):
                        text = parent_text
                        low = text.lower()

                except Exception:
                    break

            # Csak RTX 5080 / RTX 5070 Ti.
            if not any(
                gpu in low
                for gpu in GPU_NAMES
            ):
                continue

            # Bontott / felbontott / használt / újszerű.
            if not any(
                condition in low
                for condition in CONDITIONS
            ):
                continue

            # Terméknév.
            name = " ".join(
                link.inner_text().split()
            )

            if not name:
                lines = [
                    x.strip()
                    for x in text.split("\n")
                    if x.strip()
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

            condition = get_condition(text)

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

    return products


def scan_alza():
    products = []

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

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

        # Az Alza több oldalra bontja az outlet kínálatot.
        # 1-10. oldalt végignézzük.
        for page_number in range(1, 11):

            if page_number == 1:
                url = BASE_URL
            else:
                url = (
                    BASE_URL
                    + f"?page={page_number}"
                )

            try:
                page_products = scan_page(
                    page,
                    url,
                )

                products.extend(
                    page_products
                )

            except Exception:
                continue

        browser.close()

    # Duplikációk kiszűrése.
    unique = {}

    for product in products:
        unique[product["id"]] = product

    products = list(
        unique.values()
    )

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

    # Első futás: baseline.
    if not data.get("initialized"):

        data["seen"] = sorted(
            current_ids
        )

        data["initialized"] = True

    else:

        new_products = [
            product
            for product in products
            if product["id"]
            not in previous_seen
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

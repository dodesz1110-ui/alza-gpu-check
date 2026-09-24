import json
import os
import re
import hashlib
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from playwright.sync_api import sync_playwright


DATA_FILE = "site/data.json"

BASE_URLS = [
    "https://m.alza.hu/gaming/nvidia-rtx-5070-ti/vasar-hasznalt-termekek/u1000208444.htm",
    "https://m.alza.hu/gaming/nvidia-geforce-rtx-5080/vasar-hasznalt-termekek/u1000208445.htm",
]

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


def make_id(url, name):
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

        if 100000 <= number <= 2000000:
            prices.append(number)

    return min(prices) if prices else None


def get_condition(text):
    low = text.lower()

    if "felbontott" in low:
        return "Felbontott"

    if "bontott" in low:
        return "Bontott"

    if "újszerű" in low:
        return "Újszerű"

    if "használt" in low:
        return "Használt"

    return "Ismeretlen"


def get_stock(text):
    patterns = [
        r"raktáron\s*(?:>|:)?\s*(\d+)\s*db",
        r"raktárban\s*(?:>|:)?\s*(\d+)\s*db",
        r"(\d+)\s*db",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:
            try:
                return int(match.group(1))
            except Exception:
                pass

    return None


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


def scan_page(page, url, category):
    products = []

    print(f"Vizsgálat: {url}")

    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    page.wait_for_timeout(5000)

    # Minden terméklinket végignézünk.
    links = page.locator("a[href]")

    print(f"Talált linkek: {links.count()}")

    for i in range(links.count()):

        try:
            link = links.nth(i)

            href = link.get_attribute("href")

            if not href:
                continue

            href = urljoin(
                "https://www.alza.hu",
                href
            )

            # Csak valódi Alza termékoldalak.
            if "alza.hu" not in href:
                continue

            if ".htm" not in href:
                continue

            # Link saját szövege.
            text = " ".join(
                link.inner_text().split()
            )

            # Ha a link saját szövege kevés,
            # feljebb megyünk a DOM-ban.
            node = link

            for _ in range(6):

                low = text.lower()

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

                except Exception:
                    break

            low = text.lower()

            # Csak RTX 5080 / RTX 5070 Ti.
            if not any(
                gpu in low
                for gpu in GPU_NAMES
            ):
                continue

            # Csak outlet állapot.
            if not any(
                condition in low
                for condition in CONDITIONS
            ):
                continue

            # A termék nevét az eredeti linkből próbáljuk.
            name = " ".join(
                link.inner_text().split()
            )

            # Ha az eredeti link üres vagy nem megfelelő,
            # keressünk RTX-es sort a szülő szövegében.
            if (
                not name
                or "rtx" not in name.lower()
            ):
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

            # Túl hosszú konténerszöveg esetén
            # próbáljuk a terméknevet levágni.
            if len(name) > 180:
                match = re.search(
                    r"((?:GIGABYTE|ASUS|MSI|PALIT|GAINWARD|ZOTAC|PNY|INNO3D|KFA2|SAPPHIRE|XFX|PowerColor|AORUS)[^0-9\n]{0,140}RTX\s*(?:5080|5070\s*Ti)[^\n]*)",
                    text,
                    re.IGNORECASE,
                )

                if match:
                    name = match.group(1).strip()

            price = get_price(text)

            condition = get_condition(text)

            stock = get_stock(text)

            pid = make_id(
                href,
                name
            )

            product = {
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
            }

            # Ugyanazt a linket ne vegyük fel többször.
            if not any(
                p["id"] == pid
                for p in products
            ):
                products.append(product)

                print(
                    f"MEGTALÁLVA: "
                    f"{name} | "
                    f"{condition} | "
                    f"{price} Ft | "
                    f"{stock} db"
                )

        except Exception:
            continue

    return products


def scan_alza():
    all_products = []

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

        for base_url in BASE_URLS:

            category = (
                "RTX 5070 Ti"
                if "5070-ti" in base_url
                else "RTX 5080"
            )

            for page_number in range(1, 11):

                if page_number == 1:
                    url = base_url
                else:
                    url = (
                        base_url
                        + f"?page={page_number}"
                    )

                try:
                    found = scan_page(
                        page,
                        url,
                        category
                    )

                    all_products.extend(found)

                except Exception as e:
                    print(
                        f"Hiba az oldalon: {url}"
                    )
                    print(e)

        browser.close()

    # Duplikációk kiszűrése.
    unique = {}

    for product in all_products:
        unique[product["id"]] = product

    products = list(
        unique.values()
    )

    products.sort(
        key=lambda x: (
            x["price"] is None,
            x["price"] or 0
        )
    )

    print(
        f"ÖSSZES TALÁLT TERMÉK: {len(products)}"
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

    # Első sikeres futás = baseline.
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

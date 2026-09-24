import json, os, re, hashlib
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

TARGETS = [
    ("RTX 5080", "https://m.alza.hu/gaming/nvidia-geforce-rtx-5080/vasar-hasznalt-termekek/u1000208445.htm"),
    ("RTX 5070 Ti", "https://m.alza.hu/gaming/nvidia-rtx-5070-ti/vasar-hasznalt-termekek/u1000208444.htm"),
]

DATA_FILE = "site/data.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/140 Mobile Safari/537.36",
    "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.8",
}

def money(s):
    m = re.search(r"([\d .]+)\s*Ft", s or "")
    return int(re.sub(r"\D", "", m.group(1))) if m else None

def product_id(url, name):
    return hashlib.sha256((url + "|" + name).encode()).hexdigest()[:20]

def extract(html, category):
    soup = BeautifulSoup(html, "html.parser")
    found = {}

    # Alza markup változhat, ezért többféle jel alapján keresünk.
    for a in soup.find_all("a", href=True):
        name = " ".join(a.get_text(" ", strip=True).split())
        if not name:
            continue
        low = name.lower()
        if "rtx 5080" not in low and "rtx 5070 ti" not in low:
            continue

        node = a
        block_text = name
        for _ in range(6):
            node = node.parent if node.parent else node
            txt = " ".join(node.get_text(" ", strip=True).split())
            if 30 < len(txt) < 3000:
                block_text = txt
                if re.search(r"felbontott|újszerű|használt", txt, re.I):
                    break

        if not re.search(r"felbontott|újszerű|használt", block_text, re.I):
            continue

        href = urljoin("https://www.alza.hu", a["href"])
        price = money(block_text)

        stock = None
        sm = re.search(r"(?:raktáron|raktáron\s*>)\s*(\d+)\s*db", block_text, re.I)
        if sm:
            stock = int(sm.group(1))

        # Csak valódi termékoldalakat próbálunk megtartani.
        if "alza.hu" not in href or href.endswith(".htm") is False:
            continue

        pid = product_id(href, name)
        found[pid] = {
            "id": pid,
            "category": category,
            "name": name,
            "price": price,
            "stock": stock,
            "url": href,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    return list(found.values())

def load():
    if not os.path.exists(DATA_FILE):
        return {"products": [], "seen": [], "initialized": False, "last_check": None, "error": None}
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"products": [], "seen": [], "initialized": False, "last_check": None, "error": None}

def save(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def github_issue(title, body):
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    if not token or not repo:
        return
    r = requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"title": title, "body": body, "labels": ["alza-gpu-watch"]},
        timeout=20,
    )
    r.raise_for_status()

def main():
    data = load()
    products = []
    errors = []

    for category, url in TARGETS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            products.extend(extract(r.text, category))
        except Exception as e:
            errors.append(f"{category}: {e}")

    # Duplikációk kiszűrése.
    unique = {p["id"]: p for p in products}
    products = sorted(unique.values(), key=lambda p: ((p["price"] is None), p["price"] or 0))

    previous_seen = set(data.get("seen", []))
    current_ids = {p["id"] for p in products}

    # Első futás: csak baseline, ne küldjön hamis "új" riasztást.
    if not data.get("initialized"):
        data["seen"] = sorted(current_ids)
        data["initialized"] = True
    else:
        new_items = [p for p in products if p["id"] not in previous_seen]
        for p in new_items:
            price = f'{p["price"]:,}'.replace(",", " ") + " Ft" if p["price"] else "ár nem olvasható"
            github_issue(
                f"🟢 Új Alza Outlet {p['category']}: {p['name']}",
                f"Új bontott/felbontott {p['category']} találat az Alzán.\n\n"
                f"**Ár:** {price}\n\n"
                f"**Közvetlen link:** {p['url']}\n\n"
                f"**Készlet:** {p.get('stock') or 'nincs megadva'}\n"
            )
        data["seen"] = sorted(previous_seen | current_ids)

    data["products"] = products
    data["last_check"] = datetime.now(timezone.utc).isoformat()
    data["error"] = " | ".join(errors) if errors else None
    save(data)

if __name__ == "__main__":
    main()

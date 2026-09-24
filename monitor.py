import json, os, re, smtplib, ssl, html
from urllib.parse import unquote
from datetime import datetime, timezone
from email.message import EmailMessage
import requests

DATA_FILE = "site/data.json"
CATEGORY = "https://m.alza.hu/gaming/videokartyak/vasar-hasznalt-termekek/u38842862.htm"
TARGETS = ("rtx 5070 ti", "rtx 5080")
CONDITIONS = ("felbontott", "bontott", "használt", "újszerű")

S = requests.Session()
S.headers.update({"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36","Accept":"text/markdown,text/plain,*/*"})

def load_data():
    try:
        with open(DATA_FILE, encoding="utf-8") as f: d=json.load(f)
    except Exception: d={}
    d.setdefault("products", []); d.setdefault("seen", []); d.setdefault("initialized", False); d.setdefault("last_check", None); d.setdefault("error", None)
    return d

def save_data(d):
    os.makedirs("site", exist_ok=True)
    with open(DATA_FILE,"w",encoding="utf-8") as f: json.dump(d,f,ensure_ascii=False,indent=2)

def clean(s): return re.sub(r"\s+"," ",s or "").strip()

def google_search(query):
    r = S.get("https://www.google.com/search", params={
        "q": query, "hl": "hu", "num": "100", "filter": "0"
    }, timeout=30)
    r.raise_for_status()
    return r.text

def parse_google(raw):
    page = html.unescape(raw)
    found = {}
    # Google search results expose direct Alza product URLs.
    urls = re.findall(
        r'https?://(?:www\\.|m\\.)?alza\\.hu/[^"\\s<>]+/d\\d+\\.htm',
        page, re.I
    )
    for url in urls:
        url = unquote(url).replace("\\/","/").rstrip(").,;")
        pos = page.lower().find(url.lower())
        if pos < 0:
            continue
        block = clean(re.sub(r"<[^>]+>", " ", page[max(0,pos-1800):pos+2500]))
        low = block.lower()
        if not any(t in low for t in TARGETS):
            continue
        if not any(c in low for c in CONDITIONS):
            continue
        title = ""
        tm = re.search(r"<h3[^>]*>(.*?)</h3>", page[max(0,pos-1800):pos+1000], re.I|re.S)
        if tm:
            title = clean(re.sub(r"<[^>]+>", " ", html.unescape(tm.group(1))))
        if not title:
            title = "Alza RTX találat"
        pm = re.search(r"(\\d{1,3}(?:[ .]\\d{3})+|\\d{5,6})\\s*Ft", block, re.I)
        price = int(re.sub(r"\\D","",pm.group(1))) if pm else None
        condition = next((x.capitalize() for x in CONDITIONS if x in low), "Felbontott")
        clean_url = url.split("?")[0].split("#")[0]
        found[clean_url] = {
            "category": "RTX 5070 Ti" if "5070 ti" in low else "RTX 5080",
            "condition": condition, "name": title, "price": price,
            "stock": None, "url": clean_url,
            "checked_at": datetime.now(timezone.utc).isoformat()
        }
    return list(found.values())

def scan():
    queries = [
        'site:alza.hu/gaming "RTX 5070 Ti" ("Felbontott" OR "Bontott" OR "Használt" OR "Újszerű")',
        'site:alza.hu/gaming "RTX 5080" ("Felbontott" OR "Bontott" OR "Használt" OR "Újszerű")'
    ]
    allp, errors, diagnostics = {}, [], []
    for q in queries:
        try:
            raw = google_search(q)
            items = parse_google(raw)
            diagnostics.append({
                "query": q, "length": len(raw), "items": len(items),
                "alza_links": len(re.findall(r"https?://(?:www\\.|m\\.)?alza\\.hu/", html.unescape(raw), re.I))
            })
            print(f"GOOGLE: {len(items)} Alza RTX találat, chars={len(raw)}")
            for p in items:
                allp[p["url"]] = p
        except Exception as e:
            errors.append(f"{q}: {type(e).__name__}: {e}")
    products = sorted(allp.values(), key=lambda p:(p["price"] is None,p["price"] or 0))
    print("ÖSSZES TALÁLT TERMÉK:", len(products))
    return products, errors, diagnostics

def send_email(items):
    password=os.getenv("GMAIL_APP_PASSWORD")
    if not password: print("GMAIL_APP_PASSWORD nincs beállítva – email kihagyva."); return
    user="dodesz1110@gmail.com"; msg=EmailMessage()
    msg["Subject"]=f"ALZA GPU – {len(items)} új RTX"; msg["From"]=user; msg["To"]=user
    lines=["Új bontott/felbontott RTX 5070 Ti / RTX 5080 az Alzán:",""]
    for p in items:
        price=f'{p["price"]:,}'.replace(","," ")+" Ft" if p["price"] else "Nincs ár"
        lines += [p["name"],f'Ár: {price}',f'Állapot: {p["condition"]}',f'Készlet: {p["stock"] if p["stock"] is not None else "-"} db',p["url"],""]
    msg.set_content("\n".join(lines))
    with smtplib.SMTP("smtp.gmail.com",587,timeout=30) as s:
        s.starttls(context=ssl.create_default_context()); s.login(user,password); s.send_message(msg)
    print("EMAIL elküldve:",len(items))

def notify_github(p):
    token=os.getenv("GITHUB_TOKEN"); repo=os.getenv("GITHUB_REPOSITORY")
    if not token or not repo: return
    price=f'{p["price"]:,}'.replace(","," ")+" Ft" if p["price"] else "Nincs ár"
    body=f'## 🟢 Új Alza videókártya\n\n**Típus:** {p["category"]}\n\n**Állapot:** {p["condition"]}\n\n**Termék:** {p["name"]}\n\n**Ár:** {price}\n\n**Készlet:** {p.get("stock") if p.get("stock") is not None else "-"} db\n\n**Közvetlen link:** {p["url"]}\n'
    r=requests.post(f"https://api.github.com/repos/{repo}/issues",headers={"Authorization":f"Bearer {token}","Accept":"application/vnd.github+json"},json={"title":f'🟢 Új Alza {p["condition"]} {p["category"]}: {p["name"]}',"body":body},timeout=20)
    r.raise_for_status()

def main():
    d=load_data(); products,errors,diagnostics=scan(); previous=set(d.get("seen",[])); current={p["url"] for p in products}
    if not d.get("initialized"):
        d["seen"]=sorted(current); d["initialized"]=True; print("BASELINE: a mostani találatok kiindulópontként elmentve.")
    else:
        new=[p for p in products if p["url"] not in previous]; print("ÚJ TERMÉKEK:",len(new))
        if new:
            try: send_email(new)
            except Exception as e: print("EMAIL HIBA:",type(e).__name__,e)
            for p in new:
                try: notify_github(p)
                except Exception as e: print("GITHUB ÉRTESÍTÉS HIBA:",type(e).__name__,e)
        d["seen"]=sorted(previous|current)
    d["products"]=products; d["diagnostics"]=diagnostics; d["last_check"]=datetime.now(timezone.utc).isoformat(); d["error"]="; ".join(errors) if errors else None; save_data(d)

if __name__=="__main__": main()

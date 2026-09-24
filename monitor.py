import json, os, re, smtplib, ssl, html, time
from urllib.parse import unquote
from datetime import datetime, timezone
from email.message import EmailMessage
import requests

DATA_FILE = "site/data.json"
CATEGORY = "https://m.alza.hu/nvidia-geforce-rtx-50-series-videokartyak/18914811.htm?commodityWears=2%2C1%2C3%2C&param=340-239996419%2C340-239996420"
TARGETS = ("rtx 5070 ti", "rtx 5080")
CONDITIONS = ("felbontott", "bontott", "használt", "újszerű")

S = requests.Session()
S.headers.update({"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36","Accept":"text/html,text/plain,*/*"})

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

def parse_alza(raw):
    page = html.unescape(raw).replace("\\/","/")
    found = {}
    # Product pages use /dNNNNNNNN.htm. Keep only RTX 5070 Ti / 5080 items.
    urls = re.findall(r'https?://(?:www\.|m\.)?alza\.hu/[^"\s<>]+/d\d+\.htm', page, re.I)
    for url in urls:
        url = unquote(url).rstrip(").,;")
        pos = page.lower().find(url.lower())
        block = clean(re.sub(r"<[^>]+>", " ", page[max(0,pos-2200):pos+3200]))
        low = block.lower()
        if not any(t in low for t in TARGETS): continue
        condition = next((x.capitalize() for x in CONDITIONS if x in low), None)
        if not condition: continue
        title = ""
        for pat in (r'<h2[^>]*>(.*?)</h2>', r'<h3[^>]*>(.*?)</h3>', r'<a[^>]*>(.*?)</a>'):
            m=re.search(pat, page[max(0,pos-2200):pos+1800], re.I|re.S)
            if m:
                title=clean(re.sub(r"<[^>]+>"," ",html.unescape(m.group(1))))
                if title: break
        if not title: title="Alza RTX találat"
        pm=re.search(r"(\d{1,3}(?:[ .]\d{3})+|\d{5,6})\s*Ft",block,re.I)
        price=int(re.sub(r"\D","",pm.group(1))) if pm else None
        sm=re.search(r"(?:raktáron|készleten|készlet)[^\d]{0,40}(\d+)\s*(?:db|ks)",block,re.I)
        stock=int(sm.group(1)) if sm else None
        clean_url=url.split("?")[0].split("#")[0]
        found[clean_url]={"category":"RTX 5070 Ti" if "5070 ti" in low else "RTX 5080","condition":condition,"name":title,"price":price,"stock":stock,"url":clean_url,"checked_at":datetime.now(timezone.utc).isoformat()}
    return list(found.values())

def direct_alza():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(viewport={"width":390,"height":844}, user_agent="Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/140 Mobile Safari/537.36")
            page.goto(CATEGORY, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            raw=page.content()
            title=page.title()
            browser.close()
            print("ALZA DIRECT:", len(raw), "chars,", title)
            return parse_alza(raw), {"source":"direct","length":len(raw),"title":title}
    except Exception as e:
        print("ALZA DIRECT HIBA:",type(e).__name__,e)
        return [], {"source":"direct","error":f"{type(e).__name__}: {e}"}

def google_search(query):
    from playwright.sync_api import sync_playwright
    from urllib.parse import quote_plus
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(
            locale="hu-HU",
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36"
        )
        # Direct URL is more reliable in CI than filling Google's search box.
        url="https://www.google.com/search?q="+quote_plus(query)+"&hl=hu"
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        raw=page.content()
        title=page.title()
        links=page.locator("a").evaluate_all("(els)=>els.map(a=>a.href).filter(Boolean)")
        alza_links=[x for x in links if "alza.hu/" in x.lower()]
        print("GOOGLE PAGE:", len(raw), "chars,", title, "Alza linkek:", len(alza_links))
        browser.close()
        return raw

def scan():
    direct, diag = direct_alza()
    if direct:
        return sorted({p["url"]:p for p in direct}.values(),key=lambda p:(p["price"] is None,p["price"] or 0)), [], [diag]
    queries=[
        'site:alza.hu "RTX 5070 Ti" ("Felbontott" OR "Bontott" OR "Használt" OR "Újszerű")',
        'site:alza.hu "RTX 5080" ("Felbontott" OR "Bontott" OR "Használt" OR "Újszerű")'
    ]
    allp={}; errors=[]; diagnostics=[diag]
    for q in queries:
        try:
            raw=google_search(q)
            items=parse_alza(raw)
            diagnostics.append({"source":"google","query":q,"length":len(raw),"items":len(items),"alza_links":len(re.findall(r"https?://(?:www\.|m\.)?alza\.hu/",html.unescape(raw),re.I))})
            print("GOOGLE:",len(items),"Alza RTX találat")
            for p in items: allp[p["url"]]=p
        except Exception as e:
            errors.append(f"{q}: {type(e).__name__}: {e}")
    products=sorted(allp.values(),key=lambda p:(p["price"] is None,p["price"] or 0))
    print("ÖSSZES TALÁLT TERMÉK:",len(products))
    return products,errors,diagnostics

def send_email(items):
    password=os.getenv("GMAIL_APP_PASSWORD")
    if not password: print("GMAIL_APP_PASSWORD nincs beállítva – email kihagyva."); return
    user="dodesz1110-ui@gmail.com"
    user="dodesz1110@gmail.com"
    msg=EmailMessage(); msg["Subject"]=f"ALZA GPU – {len(items)} új RTX"; msg["From"]=user; msg["To"]=user
    lines=["Új bontott/felbontott RTX 5070 Ti / RTX 5080 az Alzán:",""]
    for p in items:
        price=f'{p["price"]:,}'.replace(","," ")+" Ft" if p["price"] else "Nincs ár"
        lines += [p["name"],f'Ár: {price}',f'Állapot: {p["condition"]}',f'Készlet: {p["stock"] if p["stock"] is not None else "-"} db',p["url"],""]
    msg.set_content("\n".join(lines))
    with smtplib.SMTP("smtp.gmail.com",587,timeout=30) as s:
        s.starttls(context=ssl.create_default_context()); s.login(user,password); s.send_message(msg)
    print("EMAIL elküldVE:",len(items))

def notify_github(p):
    token=os.getenv("GITHUB_TOKEN"); repo=os.getenv("GITHUB_REPOSITORY")
    if not token or not repo: return
    price=f'{p["price"]:,}'.replace(","," ")+" Ft" if p["price"] else "Nincs ár"
    body=f'## 🟢 Új Alza videókártya\n\n**Típus:** {p["category"]}\n\n**Állapot:** {p["condition"]}\n\n**Termék:** {p["name"]}\n\n**Ár:** {price}\n\n**Készlet:** {p.get("stock") if p.get("stock") is not None else "-"} db\n\n**Közvetlen link:** {p["url"]}\n'
    r=requests.post(f"https://api.github.com/repos/{repo}/issues",headers={"Authorization":f"Bearer {token}","Accept":"application/vnd.github+json"},json={"title":f'🟢 Új Alza {p["condition"]} {p["category"]}: {p["name"]}',"body":body},timeout=20); r.raise_for_status()

def main():
    d=load_data(); products,errors,diagnostics=scan(); previous=set(d.get("seen",[])); current={p["url"] for p in products}
    # Empty/failed scans never become a baseline and never generate false "new" alerts.
    if products and not previous:
        d["seen"]=sorted(current); d["initialized"]=True; print("BASELINE: a mostani valódi találatok kiindulópontként elmentve.")
    elif products:
        new=[p for p in products if p["url"] not in previous]; print("ÚJ TERMÉKEK:",len(new))
        if new:
            try: send_email(new)
            except Exception as e: print("EMAIL HIBA:",type(e).__name__,e)
            for p in new:
                try: notify_github(p)
                except Exception as e: print("GITHUB ÉRTESÍTÉS HIBA:",type(e).__name__,e)
        d["seen"]=sorted(previous|current); d["initialized"]=True
    else:
        print("NINCS MEGBÍZHATÓ TALÁLAT – baseline nem módosul.")
    d["products"]=products; d["diagnostics"]=diagnostics; d["last_check"]=datetime.now(timezone.utc).isoformat(); d["error"]="; ".join(errors) if errors else None; save_data(d)

if __name__=="__main__": main()

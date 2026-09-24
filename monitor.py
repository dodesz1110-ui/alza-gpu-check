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

def fetch_page(n):
    queries = [
        'site:alza.hu "RTX 5070 Ti" "Felbontott"',
        'site:alza.hu "RTX 5080" "Felbontott"',
        'site:alza.hu "RTX 5070 Ti" "Újszerű"',
        'site:alza.hu "RTX 5080" "Újszerű"',
        'site:alza.hu "RTX 5070 Ti" "Használt"',
        'site:alza.hu "RTX 5080" "Használt"'
    ]
    q=queries[(n-1)%len(queries)]
    r=S.get("https://www.bing.com/search",params={"q":q,"count":50,"setlang":"hu-HU","cc":"HU"},headers={"Accept":"text/html","User-Agent":"Mozilla/5.0"},timeout=60)
    r.raise_for_status()
    return r.text

def parse(md):
    found={}
    decoded=unquote(html.unescape(md)).replace("\\/", "/")
    for m in re.finditer(r"""(?:https?://)?(?:www\\.|m\\.)?alza\\.hu[^<>"'\\s&]+""", decoded, re.I):
        url=unquote(html.unescape(m.group(0))).rstrip(").,;")
        if not url.lower().startswith("http"):
            url="https://"+url
        if not re.search(r"/d\\d+\\.htm",url,re.I):
            continue
        ctx=clean(re.sub(r"<[^>]+>"," ",decoded[max(0,m.start()-5000):min(len(decoded),m.end()+5000)]))
        low=ctx.lower()
        target=next((x for x in TARGETS if x in low),None)
        if not target:
            continue
        name_m=re.search(r"([^|\\n]{0,220}RTX\\s*(?:5080|5070\\s*Ti)[^|\\n]{0,220})",ctx,re.I)
        name=clean(name_m.group(1)) if name_m else ("RTX 5070 Ti" if "5070 ti" in target else "RTX 5080")
        pm=re.findall(r"(\\d{1,3}(?:[ .]\\d{3})+|\\d{5,6})\\s*Ft",ctx,re.I)
        nums=[int(re.sub(r"\\D","",x)) for x in pm if 100000<=int(re.sub(r"\\D","",x))<=2000000]
        price=min(nums) if nums else None
        condition=next((x.capitalize() for x in CONDITIONS if x in low),"Felbontott")
        sm=re.search(r"(?:raktáron|raktárban)[^\\d]{0,40}(\\d+)\\s*db",ctx,re.I)
        stock=int(sm.group(1)) if sm else None
        key=url.split("?")[0].split("#")[0]
        found[key]={"category":"RTX 5070 Ti" if "5070 ti" in target else "RTX 5080","condition":condition,"name":name,"price":price,"stock":stock,"url":key,"checked_at":datetime.now(timezone.utc).isoformat()}
    return list(found.values())

def scan():
    allp={}; errors=[]; diagnostics=[]
    for n in range(1,11):
        try:
            raw=fetch_page(n); items=parse(raw); diagnostics.append({"page":n,"length":len(raw),"rtx5070ti":raw.lower().count("rtx 5070 ti"),"rtx5080":raw.lower().count("rtx 5080"),"links":len(re.findall(r"(?:https?://)?(?:www\\.|m\\.)?alza\\.hu/[^\\s)<>]+\\.htm",html.unescape(raw).replace("\\/","/"),re.I))}); print(f"OLDAL {n}: {len(items)} találat, chars={len(raw)}, 5070Ti={raw.lower().count("rtx 5070 ti")}, 5080={raw.lower().count("rtx 5080")}")
            for p in items: allp[p["url"]]=p
        except Exception as e: errors.append(f"oldal {n}: {type(e).__name__}: {e}")
    products=sorted(allp.values(),key=lambda p:(p["price"] is None,p["price"] or 0))
    print("ÖSSZES TALÁLT TERMÉK:",len(products))
    return products,errors,diagnostics

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

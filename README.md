# Alza GPU Watch – GitHub Pages + Actions

Ez a változat telefonról is használható.

Figyeli:
- RTX 5080 – Alza Outlet/felbontott
- RTX 5070 Ti – Alza Outlet/felbontott

A GitHub Actions ütemezetten futtatja az ellenőrzést, a GitHub Pages pedig megjeleníti az aktuális találatokat.

## Beállítás

1. Töltsd fel a teljes mappát a GitHub repository gyökerébe.
2. GitHub → Settings → Pages.
3. Build and deployment → Source: **GitHub Actions**.
4. Actions → Alza GPU Watch → **Run workflow** az első kézi teszthez.

Az első futás csak kiinduló állapotot ment, nem küld riasztást.
Később az új termékekhez GitHub Issue készül. Ha a repository értesítései engedélyezve vannak, a GitHub app/email értesítést tud küldeni.

A workflow 5 percenként fut. A GitHub dokumentációja szerint ez a scheduled workflow-k legrövidebb támogatott intervalluma.

## Fontos

Az Alza oldalának HTML-je vagy botvédelme változhat, ezért előfordulhat, hogy a lekérdezőt később módosítani kell.

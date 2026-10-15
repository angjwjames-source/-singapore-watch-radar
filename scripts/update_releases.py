#!/usr/bin/env python3
"""Update Singapore Watch Radar from public official brand pages.

This is deliberately conservative: it keeps previously verified global records,
refreshes official pages, and adds currently visible official product/listing links.
An official global press release never implies Singapore availability.
"""
from __future__ import annotations
import json, re, hashlib, os
from datetime import datetime, timezone
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

UA = "SingaporeWatchRadar/1.1 (daily public release index)"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "en-SG,en;q=0.9"})
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "releases.json")


def clean(x):
    return re.sub(r"\s+", " ", x or "").strip()


def absurl(src, base):
    if not src or src.startswith("data:"):
        return None
    return urljoin(base, src)


def page(url):
    r = S.get(url, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def date_iso(text):
    m = re.search(r"\b(20\d{2})[./-](\d{1,2})[./-](\d{1,2})\b", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b", text, re.I)
    if m:
        try:
            return datetime.strptime(m.group(0), "%d %B %Y").date().isoformat()
        except ValueError:
            pass
    return None


def meta_image(soup, base):
    for attrs in ({"property": "og:image"}, {"name": "twitter:image"}):
        t = soup.find("meta", attrs=attrs)
        if t and t.get("content"):
            return absurl(t["content"], base)
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if src and not src.startswith("data:"):
            return absurl(src, base)
    return None


def price_sgd(text):
    m = re.search(r"S\$\s*([\d,]+(?:\.\d{2})?)", text, re.I)
    return float(m.group(1).replace(",", "")) if m else None


def edition_qty(text):
    patterns = [r"limited(?:[- ]edition)?(?: model)? of\s+([\d,]+)\s+pieces", r"limited to\s+([\d,]+)\s+pieces", r"limited edition\s*(?:of)?\s*([\d,]+)\s*(?:pieces|units)"]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return f"{m.group(1)} pieces worldwide"
    return None


def make_id(brand, name, source):
    return hashlib.sha1(f"{brand}|{name}|{source}".encode()).hexdigest()[:14]


def add(items, brand, name, source, image=None, date=None, ref=None, price=None,
        sg_listing=False, limited=False, quantity=None, description="", tags=None,
        movement="Not specified", dial="See official source", size="See official source"):
    name = clean(name)
    if len(name) < 4 or not source:
        return
    rec = {
        "id": make_id(brand, name, source), "brand": brand, "name": name,
        "ref": ref, "date": date, "price": price, "movement": movement,
        "dial": dial, "size": size,
        "status": "confirmed" if sg_listing else "global",
        "sg_status": "Official Singapore catalogue listing" if sg_listing else "Global announcement · SG availability unconfirmed",
        "limited": bool(limited), "edition": quantity or ("Limited edition; quantity not stated" if limited else ""),
        "source": source, "sourceName": brand + (" Singapore" if sg_listing else " official source"),
        "image": image, "checked_at": NOW,
        "description": clean(description)[:650],
        "tags": tags or (["Limited edition"] if limited else ["Official source"])
    }
    items[rec["id"]] = rec


def seed_existing():
    try:
        obj = json.load(open(OUT, encoding="utf-8"))
        return {x["id"]: x for x in obj.get("releases", []) if x.get("id")}
    except (OSError, ValueError):
        return {}


def scrape_citizen_archive(items):
    urls = ["https://www.citizenwatch-global.com/news/2026/index.html", "https://www.citizenwatch-global.com/news/index.html"]
    for archive in urls:
        try:
            soup = page(archive)
        except Exception as e:
            print(f"WARN Citizen archive {archive}: {e}")
            continue
        links = []
        for a in soup.select("a[href]"):
            href = absurl(a.get("href"), archive)
            title = clean(a.get_text(" ", strip=True))
            if href and title and re.search(r"citizenwatch-global\.com/news/20\d{2}/", href) and len(title) >= 10 and title.lower() not in {"image", "read more"}:
                links.append((title, href))
        # Take newest links first but keep a modest cap to avoid crawling whole archive.
        for fallback_title, href in links[:35]:
            try:
                detail = page(href)
                h = detail.find(["h1", "h2"])
                title = clean(h.get_text(" ", strip=True)) if h else fallback_title
                body = clean(detail.get_text(" ", strip=True))
                refs = re.findall(r"\b[A-Z]{1,10}[- ]?[A-Z]?\d{2,5}-\d{2}[A-Z]?\b", body)
                q = edition_qty(body)
                limited = bool(re.search(r"limited[- ]edition", title + " " + body, re.I))
                # Only use a date from date-shaped URL segment if body doesn't explicitly include one.
                d = date_iso(body)
                if not d:
                    m = re.search(r"/(20\d{2})(\d{2})(\d{2})/", href)
                    if m: d = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                img = meta_image(detail, href)
                add(items, "Citizen", title, href, img, d, refs[0] if refs else None, None,
                    False, limited, q, body[:560], ["Official announcement"] + (["Limited edition"] if limited else []))
            except Exception as e:
                print(f"WARN Citizen detail {href}: {e}")


def scrape_orient_archive(items):
    base = "https://orient-watch.com/en/news/"
    try:
        soup = page(base)
    except Exception as e:
        print(f"WARN Orient archive: {e}")
        return
    links = []
    for a in soup.select("a[href]"):
        href = absurl(a.get("href"), base)
        title = clean(a.get_text(" ", strip=True))
        if href and title and re.search(r"orient-watch\.com/en/news/detail/", href) and len(title) >= 10:
            if href not in [x[1] for x in links]: links.append((title, href))
    # Archive page can feature latest Orient and Orient Star announcements together.
    for fallback_title, href in links[:40]:
        try:
            detail = page(href)
            h = detail.find(["h1", "h2"])
            title = clean(h.get_text(" ", strip=True)) if h else fallback_title
            body = clean(detail.get_text(" ", strip=True))
            brand = "Orient Star" if re.search(r"Orient Star", title + " " + body[:1200], re.I) else "Orient"
            q = edition_qty(body)
            limited = bool(re.search(r"limited[- ]edition|limited to\s+[\d,]+\s+pieces", title + " " + body, re.I))
            d = date_iso(body)
            # Anchor href paths sometimes use numeric IDs; use the first visible date only.
            add(items, brand, title, href, meta_image(detail, href), d, None, None,
                False, limited, q, body[:560], ["Official announcement"] + (["Limited edition"] if limited else []))
        except Exception as e:
            print(f"WARN Orient detail {href}: {e}")


def scrape_tissot(items):
    url = "https://www.tissotwatches.com/en-sg/collection/selections/novelties.html"
    try:
        soup = page(url)
    except Exception as e:
        print(f"WARN Tissot Singapore catalogue: {e}")
        return
    for a in soup.select("a[href]"):
        href = absurl(a.get("href"), url)
        if not href or "tissotwatches.com/en-sg/" not in href:
            continue
        node = a
        best = None
        for _ in range(5):
            if node is None: break
            text = clean(node.get_text(" ", strip=True))
            if re.search(r"S\$\s*[\d,]+", text) and ("/products/" in href or "/collection/" in href):
                best = node
                break
            node = node.parent
        if not best:
            continue
        card_text = clean(best.get_text(" ", strip=True))
        price = price_sgd(card_text)
        imgs = best.find_all("img")
        image = None
        for im in imgs:
            image = absurl(im.get("src") or im.get("data-src") or im.get("data-lazy-src") or im.get("srcset", "").split(" ")[0], url)
            if image: break
        name = clean(a.get("aria-label") or a.get("title") or a.get_text(" ", strip=True))
        if name.lower() in {"", "image"}:
            name = clean(card_text)
        name = re.sub(r"\bS\$\s*[\d,]+(?:\.\d{2})?\b", "", name)
        name = re.sub(r"^(New|Special Edition|New Special Edition)\s*[•·-]?\s*", "", name, flags=re.I)
        if len(name) < 4: continue
        limited = bool(re.search(r"special edition|limited edition", card_text, re.I))
        add(items, "Tissot", name, href, image, None, None, price, True, limited, None,
            "Current listing from the official Tissot Singapore novelties catalogue. Check product page for exact reference and stock.",
            ["Singapore price", "Official new style"])


def scrape_citizen_singapore(items):
    # Use only explicitly listed official local pages; do not infer local availability from global news.
    for base in ["https://citizen.com.sg/news/", "https://citizen.com.sg/collections/new-arrivals"]:
        try: soup = page(base)
        except Exception as e:
            print(f"WARN Citizen Singapore {base}: {e}")
            continue
        for a in soup.select("a[href]"):
            href = absurl(a.get("href"), base)
            title = clean(a.get_text(" ", strip=True))
            if not href or "citizen.com.sg" not in href or len(title) < 8 or href.rstrip("/") == base.rstrip("/"):
                continue
            # Product/article details are parsed only if the link appears to be a specific item.
            if not any(k in href.lower() for k in ["product", "news", "blog", "article", "collections"]):
                continue
            try:
                detail = page(href)
                h = detail.find(["h1", "h2"])
                name = clean(h.get_text(" ", strip=True)) if h else title
                body = clean(detail.get_text(" ", strip=True))
                price = price_sgd(body)
                refs = re.findall(r"\b[A-Z]{1,10}[- ]?[A-Z]?\d{2,5}-\d{2}[A-Z]?\b", body)
                limited = bool(re.search(r"limited[- ]edition", name + " " + body, re.I))
                add(items, "Citizen", name, href, meta_image(detail, href), date_iso(body), refs[0] if refs else None,
                    price, True, limited, edition_qty(body), body[:560], ["Official Singapore page"])
            except Exception as e:
                print(f"WARN Citizen SG detail {href}: {e}")


def main():
    items = seed_existing()
    successful = 0
    for fn in (scrape_citizen_archive, scrape_orient_archive, scrape_tissot, scrape_citizen_singapore):
        before = len(items)
        try:
            fn(items)
            successful += 1
            print(f"OK {fn.__name__}: {len(items)-before} new/updated records")
        except Exception as e:
            print(f"WARN {fn.__name__}: {e}")
    records = sorted(items.values(), key=lambda x: (x.get("date") or "", x.get("brand", ""), x.get("name", "")), reverse=True)
    data = {"last_checked_at": NOW, "source_count": successful, "data_mode": "scheduled-source-sync", "releases": records}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(records)} official-source records to data/releases.json")
    if successful == 0:
        raise SystemExit("All source checks failed; retaining only previously saved records. See warnings above.")

if __name__ == "__main__":
    main()

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
    # Prefer explicit social/product artwork. A random first page image is often
    # a logo, navigation thumbnail, or unrelated watch and must not be shown.
    for attrs in ({"property": "og:image"}, {"name": "twitter:image"}):
        t = soup.find("meta", attrs=attrs)
        if t and t.get("content"):
            candidate = absurl(t["content"], base)
            if candidate and not re.search(r"dummy|placeholder|logo|menu-push|sprite|banner", candidate, re.I):
                return candidate
    # Parse structured product metadata when a page exposes it outside social tags.
    for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = node.string or node.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        stack = payload if isinstance(payload, list) else [payload]
        while stack:
            item = stack.pop(0)
            if isinstance(item, dict):
                image = item.get("image")
                values = [image] if isinstance(image, str) else image if isinstance(image, list) else [image.get("url")] if isinstance(image, dict) and image.get("url") else []
                for value in values:
                    if isinstance(value, dict):
                        value = value.get("url")
                    candidate = absurl(value, base) if isinstance(value, str) else None
                    if candidate and not re.search(r"dummy|placeholder|logo|menu-push|sprite|banner", candidate, re.I):
                        return candidate
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
            elif isinstance(item, list):
                stack.extend(item)
    candidates = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        alt = clean(img.get("alt", ""))
        if not src or src.startswith("data:") or re.search(r"logo|menu|banner|icon|arrow|flag|search", alt, re.I):
            continue
        candidate = absurl(src, base)
        if candidate and not re.search(r"dummy|placeholder|logo|menu-push|sprite|banner|icon", candidate, re.I):
            if re.search(r"watch|timepiece|model|collection|limited|attesa|promaster|series 8|orient|tissot", alt, re.I):
                candidates.append(candidate)
    return candidates[0] if candidates else None


def valid_watch_title(title):
    title = clean(title)
    if len(title) < 8:
        return False
    # Exclude non-watch corporate announcements and generic navigation labels.
    if re.search(r"\b(award|sponsorship|sponsor|guarantee|warranty|customers using|dedicated apps|innovation|corporate|financial results|page number|skip to main content)\b", title, re.I):
        return False
    blocked = {"brand", "skip to main content", "skip to footer content", "page number 1",
               "page number 2", "read more", "news", "orient", "orient star", "citizen", "tissot"}
    return title.lower() not in blocked and not re.search(r"^(page\\s+\\d+|skip to |menu$|search$)", title, re.I)


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



def release_description(name, brand, sg_listing=False):
    """Write a short human-readable summary; never store scraped navigation text."""
    title = clean(name).replace("|", " ")
    title = re.sub(r"^\\d{1,2}(?:st|nd|rd|th)?\\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\\s+20\\d{2}\\s*", "", title, flags=re.I)
    if re.search(r"NK5020-58L", title, re.I):
        return "The Zenshin 60 pairs a lightweight Super Titanium case and integrated bracelet with a brushed, gradient light-blue dial and small-seconds subdial. Its automatic Calibre 8322 offers about 60 hours of power reserve, sapphire crystal and 100 m water resistance."
    if re.search(r"NJ0200-50E", title, re.I):
        return "The limited-edition TSUYOSA “Time Slip” has a compact 37 mm stainless-steel case and integrated bracelet, a textured black dial with rose-gold-tone details, and a magnified date display. It uses the automatic Calibre 8210 and is water-resistant to 50 m."
    if re.search(r"PC1017-88Y", title, re.I):
        return "The Sakura limited edition features a 34 mm pink-gold-tone case and bracelet, a mother-of-pearl dial with a soft pink gradation, and a cherry-blossom-shaped open-heart aperture. Its automatic Calibre 8229 is paired with sapphire crystal and 100 m water resistance; worldwide production is limited to 1,900 pieces."
    if re.search(r"Damascus Steel and Forged Carbon", title, re.I):
        return "This Series 8 limited edition combines Damascus steel and forged carbon in a distinctive contemporary design. The announced edition is limited to 1,300 pieces worldwide; check the official release for full specifications and local availability."
    if re.search(r"Tosa Washi Paper|Indigo Washi|Washi Dials", title, re.I):
        return "This The CITIZEN limited edition highlights a Japanese washi-paper dial with a distinctive textured finish. Selected models in the range use high-accuracy Eco-Drive technology; refer to the official announcement for the exact model specification and edition details."
    if re.search(r"Green Flash|Aurora", title, re.I):
        return "A Citizen Attesa 50th-anniversary limited edition inspired by the green flash and aurora. See the official announcement for movement and dial details; Singapore availability has not been confirmed."
    if re.search(r"RAINELL", title, re.I):
        return "Citizen expands the RAINELL collection with new designs, including a limited-edition model featuring sparkling diamonds. Refer to the official release for individual model details and regional availability."
    if re.search(r"TSUYOSA Shore", title, re.I):
        return "This TSUYOSA Shore limited edition draws on a retro-inspired design theme. Check the official release for the model’s specific design details, launch timing and Singapore availability."
    if re.search(r"Yacht Racing|PROMASTER", title, re.I):
        return "A sport-focused Citizen PROMASTER release designed around practical timing and outdoor use. Consult the official announcement for the model-specific functions, specifications and regional release details."
    if re.search(r"LIGHT in BLACK", title, re.I):
        return "Part of Citizen’s Eco-Drive 50th-anniversary LIGHT in BLACK series, this model uses a dark, light-inspired design theme. Check the official release for model-specific specifications and availability."
    if re.search(r"Star Wars|Mandalorian|Beskar", title, re.I):
        return "A Citizen ATTESA collaboration inspired by Star Wars and The Mandalorian, with design details referencing Beskar. See the official release for the full specification and edition information."
    if re.search(r"75th Anniversary|75th anniversary", title, re.I):
        return "An Orient Star release celebrating the brand’s 75th anniversary with a special-edition design. Check the official announcement for individual references, production quantities and Singapore availability."
    if re.search(r"Autumn/Winter 2026|2026 AW", title, re.I):
        return f"{brand}’s Autumn/Winter 2026 announcement introduces new models for the season. Individual references, specifications and Singapore availability vary by model; use the official release for details."
    if re.search(r"Flusso", title, re.I):
        return "Orient introduces Flusso, a modern reinterpretation of its distinctive SK-inspired design. Check the official announcement for available variations and model specifications."
    if re.search(r"TSUYOSA 60", title, re.I):
        return "The TSUYOSA 60 collection brings a sport-casual automatic design with a contemporary dial and integrated-bracelet look. Consult the official announcement for the exact reference and specifications."
    if sg_listing:
        return f"{title} is listed in the official {brand} Singapore catalogue. Check the linked product page for current specifications and local availability."
    clean_title = re.sub(r"^\\d{1,2}(?:st|nd|rd|th)?\\s+", "", title)
    clean_title = re.sub(r"^20\\d{2}\\s+", "", clean_title)
    return f"{clean_title.rstrip('. ')}. Refer to the official source for full specifications, launch details and Singapore availability."


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
                if not valid_watch_title(title):
                    title = fallback_title
                if not valid_watch_title(title):
                    print(f"INFO skipped generic Citizen title: {title!r}")
                    continue
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
                    False, limited, q, release_description(title, "Citizen"), ["Official announcement"] + (["Limited edition"] if limited else []))
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
            if not valid_watch_title(title):
                title = fallback_title
            if not valid_watch_title(title):
                print(f"INFO skipped generic Orient title: {title!r}")
                continue
            body = clean(detail.get_text(" ", strip=True))
            brand = "Orient Star" if re.search(r"Orient Star", title + " " + body[:1200], re.I) else "Orient"
            q = edition_qty(body)
            limited = bool(re.search(r"limited[- ]edition|limited to\s+[\d,]+\s+pieces", title + " " + body, re.I))
            d = date_iso(body)
            # Anchor href paths sometimes use numeric IDs; use the first visible date only.
            add(items, brand, title, href, meta_image(detail, href), d, None, None,
                False, limited, q, release_description(title, brand), ["Official announcement"] + (["Limited edition"] if limited else []))
        except Exception as e:
            print(f"WARN Orient detail {href}: {e}")


def scrape_tissot(items):
    """Collect only individual Tissot Singapore product pages linked from the novelties page."""
    base = "https://www.tissotwatches.com/en-sg/collection/selections/novelties.html"
    try:
        soup = page(base)
    except Exception as e:
        print(f"WARN Tissot Singapore catalogue: {e}")
        return
    links = []
    for a in soup.select('a[href*="/en-sg/products/"]'):
        href = absurl(a.get("href"), base)
        if href and href not in links:
            links.append(href)
    print(f"INFO Tissot: found {len(links)} individual product links")
    for href in links[:50]:
        try:
            detail = page(href)
            h = detail.find("h1")
            name = clean(h.get_text(" ", strip=True)) if h else ""
            if not valid_watch_title(name):
                continue
            body = clean(detail.get_text(" ", strip=True))
            price = price_sgd(body)
            # Avoid treating a generic collection price as a product price.
            refs = re.findall(r"\b[A-Z0-9]{2,5}-\\d{3}-\\d{2}-\\d{3}-\\d{2}\\b", body)
            img = meta_image(detail, href)
            limited = bool(re.search(r"special edition|limited edition", name + " " + body, re.I))
            add(items, "Tissot", name, href, img, date_iso(body), refs[0] if refs else None,
                price, True, limited, edition_qty(body), release_description(name, "Tissot", True),
                ["Singapore product page"] + (["Limited edition"] if limited else []))
        except Exception as e:
            print(f"WARN Tissot product {href}: {e}")

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
                if not valid_watch_title(name):
                    print(f"INFO skipped generic Citizen SG title: {name!r}")
                    continue
                body = clean(detail.get_text(" ", strip=True))
                price = price_sgd(body)
                refs = re.findall(r"\b[A-Z]{1,10}[- ]?[A-Z]?\d{2,5}-\d{2}[A-Z]?\b", body)
                limited = bool(re.search(r"limited[- ]edition", name + " " + body, re.I))
                add(items, "Citizen", name, href, meta_image(detail, href), date_iso(body), refs[0] if refs else None,
                    price, True, limited, edition_qty(body), release_description(name, "Citizen", True), ["Official Singapore page"])
            except Exception as e:
                print(f"WARN Citizen SG detail {href}: {e}")


def product_page_for(rec):
    """Return a model-specific official product page when a reference is known."""
    ref = clean(rec.get("ref", ""))
    brand = rec.get("brand", "")
    if not ref:
        return None
    if brand == "Citizen":
        return "https://www.citizenwatch.com/us/en/product/" + ref
    if brand == "Orient Star" and ref == "RE-BR0001N":
        return "https://orient-watch.com/en/orientstar/collection/flagship/orientstar-skeleton/" + ref + "/"
    if brand == "Orient Star" and ref.startswith("RE-BQ"):
        return "https://orient-watch.com/en/orientstar/collection/contemporary/three-hand/" + ref + "/"
    if brand == "Orient" and ref.startswith("RA-BB02"):
        return "https://orient-watch.com/en/orient/collection/contemporary/flusso/" + ref + "/"
    return None


def repair_missing_images(items):
    """Try official source pages for real images, then use a clearly labelled local fallback."""
    cache = {}
    fallback = {
        "Citizen": "assets/watch-fallback-citizen.svg",
        "Tissot": "assets/watch-fallback-tissot.svg",
        "Orient": "assets/watch-fallback-orient.svg",
        "Orient Star": "assets/watch-fallback-orient-star.svg",
    }
    for rec in items.values():
        img = rec.get("image")
        if img and not rec.get("image_fallback") and not re.search(r"dummy|placeholder|menu-push|sprite|watch-fallback", img, re.I):
            continue
        # Prefer a model-specific product page over a broad announcement page.
        sources = [product_page_for(rec), rec.get("source")]
        recovered = None
        for source in [x for x in sources if x]:
            if source not in cache:
                try:
                    soup = page(source)
                    recovered_image = meta_image(soup, source)
                    if not recovered_image and source == product_page_for(rec):
                        for im in soup.find_all("img"):
                            raw = im.get("src") or im.get("data-src") or im.get("data-lazy-src")
                            alt = clean(im.get("alt", ""))
                            candidate = absurl(raw, source)
                            if candidate and not re.search(r"logo|menu|banner|icon|arrow|flag|search|sprite|placeholder", candidate + " " + alt, re.I):
                                recovered_image = candidate
                                break
                    cache[source] = recovered_image
                except Exception as e:
                    print(f"WARN image recovery {source}: {e}")
                    cache[source] = None
            if cache[source]:
                recovered = cache[source]
                break
        if recovered:
            rec["image"] = recovered
            rec.pop("image_fallback", None)
            rec["image_source"] = "model-specific official product page"
            continue
        rec["image"] = fallback.get(rec.get("brand"), "assets/watch-fallback.svg")
        rec["image_fallback"] = True


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
    # Validate scraped records and collapse the same model discovered from multiple links.
    cleaned = {}
    for rec in items.values():
        if not valid_watch_title(rec.get("name", "")):
            continue
        if rec.get("image") and re.search(r"dummy|placeholder|menu-push", rec["image"], re.I):
            rec["image"] = None
        price = rec.get("price")
        if price is not None and (not isinstance(price, (int, float)) or price <= 0 or price > 100000):
            rec["price"] = None
        # Deduplicate a model discovered through both a press release and a catalogue.
        ref = clean(rec.get("ref", "")).lower()
        key = (clean(rec.get("brand", "")).lower(), ref) if ref else (
            clean(rec.get("brand", "")).lower(), clean(rec.get("name", "")).lower())
        old = cleaned.get(key)
        if old is None:
            cleaned[key] = rec
        else:
            old_name, new_name = clean(old.get("name", "")), clean(rec.get("name", ""))
            old_headline = len(old_name) > 95 or bool(re.match(r"^\d{1,2}\s", old_name))
            new_headline = len(new_name) > 95 or bool(re.match(r"^\d{1,2}\s", new_name))
            if (old_headline and not new_headline) or (not old.get("image") and rec.get("image")) or (old.get("image_fallback") and rec.get("image") and not rec.get("image_fallback")):
                merged = dict(old)
                merged.update({k: v for k, v in rec.items() if v not in (None, "", [])})
                if not new_headline:
                    merged["name"] = new_name
                if rec.get("image") and not rec.get("image_fallback"):
                    merged["image"] = rec["image"]
                    merged.pop("image_fallback", None)
                    if rec.get("image_source"):
                        merged["image_source"] = rec["image_source"]
                cleaned[key] = merged
            else:
                for field in ("image", "price", "ref", "date"):
                    if not old.get(field) and rec.get(field):
                        old[field] = rec[field]
    fallback = {
        "Citizen": "assets/watch-fallback-citizen.svg",
        "Tissot": "assets/watch-fallback-tissot.svg",
        "Orient": "assets/watch-fallback-orient.svg",
        "Orient Star": "assets/watch-fallback-orient-star.svg",
    }
    for rec in cleaned.values():
        if not rec.get("image"):
            rec["image"] = fallback.get(rec.get("brand"), "assets/watch-fallback.svg")
            rec["image_fallback"] = True
    records = sorted(cleaned.values(), key=lambda x: (x.get("date") or "", x.get("brand", ""), x.get("name", "")), reverse=True)
    data = {"last_checked_at": NOW, "source_count": successful, "data_mode": "scheduled-source-sync", "releases": records}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(records)} official-source records to data/releases.json")
    if successful == 0:
        raise SystemExit("All source checks failed; retaining only previously saved records. See warnings above.")

if __name__ == "__main__":
    main()

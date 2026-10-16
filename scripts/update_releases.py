#!/usr/bin/env python3
"""Update Singapore Watch Radar from public official brand pages.

This is deliberately conservative: it keeps previously verified global records,
refreshes official pages, and adds currently visible official product/listing links.
An official global press release never implies Singapore availability.
"""
from __future__ import annotations
import json, re, hashlib, os
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

UA = "SingaporeWatchRadar/1.2 (daily public release index)"
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
    r = S.get(url, timeout=12)
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


def image_url_from_tag(tag, base):
    """Resolve the best URL exposed by a lazy-loaded or responsive image tag."""
    srcset = tag.get("data-srcset") or tag.get("srcset")
    if srcset:
        choices = []
        for part in srcset.split(","):
            bits = part.strip().split()
            if bits:
                url = absurl(bits[0], base)
                if url:
                    score = 0
                    if len(bits) > 1:
                        m = re.match(r"(\d+)(w|x)", bits[1])
                        if m:
                            score = int(m.group(1))
                    choices.append((score, url))
        if choices:
            return max(choices)[1]
    for key in ("data-src", "data-lazy-src", "data-original", "data-image", "src"):
        value = tag.get(key)
        if value and not value.startswith("data:"):
            return absurl(value, base)
    return None


IMAGE_BAD = re.compile(
    r"(?:dummy|placeholder|no[-_ ]?image|default[-_ ]?image|watch-fallback|logo|"
    r"menu-push|sprite|banner|icon|arrow|flag|search|avatar|favicon|header_mega|"
    r"gravity=center|history-mobile|zoomincursorwithoutbg|/360[.]svg(?:[?#]|$)|"
    r"gift-finder-mobile|mouse_img_75th|kv_01_sp|sponsorship-footer|"
    r"[.]svg(?:[?#]|$))",
    re.I,
)
_IMAGE_CHECK_CACHE = {}


def image_is_generic(url, alt=""):
    if not url or not str(url).startswith(("http://", "https://")):
        return True
    return bool(IMAGE_BAD.search(str(url) + " " + clean(alt)))


def verify_image_url(url):
    """Verify that an official photo URL returns a raster image, not an icon or page."""
    if image_is_generic(url):
        return False
    if url in _IMAGE_CHECK_CACHE:
        return _IMAGE_CHECK_CACHE[url]
    valid = False
    try:
        origin = re.match(r"^https?://[^/]+", url, re.I)
        headers = {"Referer": origin.group(0) + "/"} if origin else {}
        with S.get(url, timeout=8, stream=True, headers=headers) as response:
            ctype = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            length = response.headers.get("Content-Length")
            if response.status_code == 200 and ctype.startswith("image/") and "svg" not in ctype:
                if not length or not length.isdigit() or int(length) >= 1200:
                    sample = next(response.iter_content(chunk_size=64), b"")
                    if sample and not sample.lstrip().lower().startswith((b"<html", b"<!doctype", b"<?xml", b"<svg")):
                        valid = True
    except Exception as exc:
        print(f"WARN image verification {url}: {exc}")
    _IMAGE_CHECK_CACHE[url] = valid
    return valid


def meta_image(soup, base):
    for attrs in ({"property": "og:image"}, {"name": "twitter:image"}, {"property": "og:image:url"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            candidate = absurl(tag["content"], base)
            if candidate and not image_is_generic(candidate):
                return candidate
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
                image = item.get("image") or item.get("thumbnailUrl")
                values = [image] if isinstance(image, str) else image if isinstance(image, list) else [image.get("url") or image.get("contentUrl")] if isinstance(image, dict) else []
                for value in values:
                    if isinstance(value, dict):
                        value = value.get("url") or value.get("contentUrl")
                    candidate = absurl(value, base) if isinstance(value, str) else None
                    if candidate and not image_is_generic(candidate):
                        return candidate
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
            elif isinstance(item, list):
                stack.extend(item)
    candidates = []
    for img in soup.find_all("img"):
        candidate = image_url_from_tag(img, base)
        alt = clean(img.get("alt", ""))
        if not candidate or image_is_generic(candidate, alt):
            continue
        if re.search(r"watch|timepiece|model|collection|limited|attesa|promaster|series 8|orient|dial|automatic", alt + " " + candidate, re.I):
            candidates.append(candidate)
    return candidates[0] if candidates else None


def image_reference_conflicts(url, rec):
    """Detect a model-specific product-photo filename that names another reference."""
    ref = re.sub(r"[^a-z0-9]", "", clean(rec.get("ref", "")).lower())
    if not url or not ref:
        return False
    # Restrict this check to recognizable model-code filename patterns, not generic
    # editorial filenames or shared collection images.
    filename = url.split("?", 1)[0].rsplit("/", 1)[-1]
    match = re.search(r"([a-z]{1,4}\d{4,5}[-_]\d{2}[a-z]?)", filename, re.I)
    if not match:
        return False
    image_ref = re.sub(r"[^a-z0-9]", "", match.group(1).lower())
    return len(image_ref) >= 8 and image_ref != ref


def model_image(soup, base, rec):
    """Prefer an image whose URL or alt text identifies the requested model."""
    ref = clean(rec.get("ref", ""))
    name = clean(rec.get("name", ""))
    ref_norm = re.sub(r"[^a-z0-9]", "", ref.lower())
    generic_tokens = {
        "citizen", "orient", "star", "watch", "watches", "timepiece",
        "automatic", "quartz", "limited", "edition", "model", "collection",
        "official", "new", "the", "with", "and", "for", "from", "series", "mm",
        "view", "front", "profile", "back", "image", "product", "three", "hand",
        "classic", "contemporary", "sports", "mechanical", "solar", "eco", "drive",
    }
    tokens = [re.sub(r"[^a-z0-9]", "", x.lower()) for x in re.findall(r"[A-Za-z0-9]+", name)]
    tokens = [x for x in tokens if len(x) > 2 and x not in generic_tokens]
    candidates = []
    for img in soup.find_all("img"):
        url = image_url_from_tag(img, base)
        alt = clean(img.get("alt", ""))
        if not url or image_is_generic(url, alt):
            continue
        context = " ".join([url, alt, clean(img.get("title", ""))]).lower()
        normalized = re.sub(r"[^a-z0-9]", "", context)
        score = 200 if ref_norm and len(ref_norm) >= 6 and ref_norm in normalized else 0
        score += 25 * len({token for token in tokens if token in normalized})
        if score:
            if re.search(r"front|main|primary", alt, re.I):
                score += 8
            if re.search(r"profile|back view|strap zoom|wrist|box|packaging", alt, re.I):
                score -= 12
            candidates.append((score, url))
    if candidates:
        return sorted(candidates, key=lambda item: item[0], reverse=True)[0][1]
    product_page = bool(re.search(r"/T[A-Z0-9]{8,}[.]html(?:$|[?#])|/(?:RE|RA)-[A-Z0-9]{5,}/?(?:$|[?#])", base, re.I))
    if product_page:
        candidate = meta_image(soup, base)
        if candidate and (not ref_norm or ref_norm in re.sub(r"[^a-z0-9]", "", candidate.lower())):
            return candidate
        return None
    if "/news/" in base.lower() or "/detail/" in base.lower():
        return meta_image(soup, base)
    return None




def strip_release_date(title):
    """Remove a publication-date prefix from a release headline for cleaner cards."""
    title = clean(title)
    return re.sub(
        r"^(?:\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+20\d{2}|20\d{2}[./-]\d{1,2}[./-]\d{1,2})\s+",
        "", title, flags=re.I
    ).strip()

def valid_watch_title(title):
    title = clean(title)
    if len(title) < 8:
        return False
    # Exclude non-watch corporate announcements and generic navigation labels.
    if re.search(r"\b(award|sponsorship|sponsor|guarantee|warranty|customers using|dedicated apps|innovation|corporate|financial results|page number|skip to main content)\b", title, re.I):
        return False
    blocked = {"brand", "skip to main content", "skip to footer content", "page number 1",
               "page number 2", "read more", "news", "orient", "orient star", "citizen",
               "products", "product", "collections", "all products", "new arrivals"}
    return title.lower() not in blocked and not re.search(r"^(page\\s+\d+|skip to |menu$|search$)", title, re.I)


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
    title = re.sub(r"^\d{1,2}(?:st|nd|rd|th)?\\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\\s+20\d{2}\\s*", "", title, flags=re.I)
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
    clean_title = re.sub(r"^\d{1,2}(?:st|nd|rd|th)?\\s+", "", title)
    clean_title = re.sub(r"^20\d{2}\\s+", "", clean_title)
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
        for fallback_title, href in links[:24]:
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
    for fallback_title, href in links[:24]:
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


def scrape_orient_catalogues(items):
    """Add individual Orient and Orient Star models from official collection pages."""
    bases = [
        ("Orient", "https://orient-watch.com/en/orient/"),
        ("Orient", "https://orient-watch.com/en/orient/collection/contemporary/"),
        ("Orient", "https://orient-watch.com/en/orient/collection/classic/"),
        ("Orient", "https://orient-watch.com/en/orient/collection/sports/"),
        ("Orient", "https://orient-watch.com/en/orient/collection/"),
        ("Orient Star", "https://orient-watch.com/en/orientstar/"),
        ("Orient Star", "https://orient-watch.com/en/orientstar/collection/contemporary/"),
        ("Orient Star", "https://orient-watch.com/en/orientstar/collection/contemporary/three-hand/"),
        ("Orient Star", "https://orient-watch.com/en/orientstar/collection/contemporary/lp/three-hand-2026aw/"),
        ("Orient Star", "https://orient-watch.com/en/orientstar/collection/flagship/orientstar-skeleton/"),
        ("Orient Star", "https://orient-watch.com/en/orientstar/collection/classic/"),
        ("Orient Star", "https://orient-watch.com/en/orientstar/collection/sports/"),
        ("Orient Star", "https://orient-watch.com/en/orientstar/collection/"),
    ]
    # Search pages expose paginated, reference-labelled model cards; extracting the
    # exact-reference image from the listing avoids dozens of slow product-page requests.
    bases.extend([
        ("Orient Star", "https://orient-watch.com/en/orientstar/search/?rt_bn_products_list_def_skip=0"),
        ("Orient Star", "https://orient-watch.com/en/orientstar/search/?rt_bn_products_list_def_skip=36"),
        ("Orient", "https://orient-watch.com/en/orient/search/?rt_bn_products_list_def_skip=0"),
        ("Orient", "https://orient-watch.com/en/orient/search/?rt_bn_products_list_def_skip=36"),
    ])
    seen = set()
    for expected_brand, base in bases:
        try:
            soup = page(base)
        except Exception as e:
            print(f"WARN {expected_brand} catalogue {base}: {e}")
            continue
        for a in soup.select("a[href]"):
            href = absurl(a.get("href"), base)
            if not href or "orient-watch.com/en/" not in href:
                continue
            match = re.search(r"/(RE-[A-Z0-9]{5,}|RA-[A-Z0-9]{5,})/?(?:[?#].*)?$", href, re.I)
            if not match:
                continue
            ref = match.group(1).upper()
            brand = "Orient Star" if ref.startswith("RE-") else "Orient"
            if brand != expected_brand or (brand, ref) in seen:
                continue
            seen.add((brand, ref))
            # Prefer the model's own thumbnail from this exact official listing page.
            title = f"{brand} {ref}"
            image = model_image(soup, base, {"ref": ref, "name": title})
            limited = bool(re.search(r"limited", clean(a.parent.get_text(" ", strip=True)) if a.parent else "", re.I))
            add(items, brand, title, href, image, None, ref, None, False,
                limited, None, release_description(title, brand),
                ["Official product catalogue"] + (["Limited edition"] if limited else []))
    print(f"INFO Orient catalogues: discovered {len(seen)} distinct model references")

def scrape_citizen_singapore(items):
    # Use official local catalogue pages, but keep the daily run bounded and reliable.
    base_urls = [
        "https://citizen.com.sg/news/",
        "https://citizen.com.sg/collections/new-arrivals",
        "https://citizen.com.sg/products/",
        "https://citizen.com.sg/products/eco-drive/",
    ]
    try:
        index = page("https://citizen.com.sg/products/")
        for link in index.select("a[href]"):
            href = absurl(link.get("href"), "https://citizen.com.sg/products/")
            label = clean(link.get_text(" ", strip=True))
            if (href and href.startswith("https://citizen.com.sg/products/")
                    and href.rstrip("/") != "https://citizen.com.sg/products"
                    and len(label) >= 4 and href not in base_urls):
                base_urls.append(href)
    except Exception as e:
        print(f"WARN Citizen category discovery: {e}")

    # Category links often repeat across the navigation and product cards. Fetch each
    # catalogue page once, and only open a bounded number of specific product pages.
    base_urls = list(dict.fromkeys(base_urls))[:18]
    detail_seen = set()
    detail_limit = 24
    for base in base_urls:
        try:
            soup = page(base)
        except Exception as e:
            print(f"WARN Citizen Singapore {base}: {e}")
            continue
        for a in soup.select("a[href]"):
            href = absurl(a.get("href"), base)
            title = clean(a.get_text(" ", strip=True))
            if not href or not href.startswith("https://citizen.com.sg/") or len(title) < 8:
                continue
            path = urlparse(href).path.strip("/")
            parts = path.split("/") if path else []
            # Product details are one path segment below /products/ or /product/.
            # Skip broad collections/category URLs; those are already scraped above.
            is_product_detail = (
                len(parts) == 2 and parts[0] == "products"
            ) or (
                len(parts) == 2 and parts[0] == "product"
            )
            if not is_product_detail or href in detail_seen:
                continue
            detail_seen.add(href)
            if len(detail_seen) > detail_limit:
                print(f"INFO Citizen Singapore detail-page cap reached ({detail_limit})")
                return
            try:
                detail = page(href)
                h = detail.find(["h1", "h2"])
                name = clean(h.get_text(" ", strip=True)) if h else title
                if not valid_watch_title(name):
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
        # Try the Singapore product route first during recovery, then the global
        # US route. Some references exist in Singapore's catalogue but not the US.
        return "https://citizen.com.sg/product/" + ref.lower() + "/"
    if brand == "Orient Star" and ref == "RE-BR0001N":
        return "https://orient-watch.com/en/orientstar/collection/flagship/orientstar-skeleton/" + ref + "/"
    if brand == "Orient Star" and ref.startswith("RE-BQ"):
        return "https://orient-watch.com/en/orientstar/collection/contemporary/three-hand/" + ref + "/"
    if brand == "Orient" and ref.startswith("RA-BB02"):
        return "https://orient-watch.com/en/orient/collection/contemporary/flusso/" + ref + "/"
    return None


def repair_missing_images(items):
    """Recover actual model/release photography from official product and catalogue pages."""
    # Catalogue pages expose exact model thumbnails even when product pages block
    # automated requests or omit social-image metadata.
    catalogue_urls = {
        "Citizen": [
            "https://citizen.com.sg/products/",
            "https://citizen.com.sg/products/the-citizen/",
            "https://citizen.com.sg/products/eco-drive/",
            "https://citizenwatch-global.com/the-citizen/lineup/5sec/product/index.html",
            "https://citizenwatch-global.com/the-citizen/lineup/5sec/AQ4106-26L.html",
            "https://citizenwatch-global.com/news/2025/20251003/index.html",
            "https://citizenwatch-global.com/news/2025/20250703/index.html",
        ],
        "Orient": [
            "https://orient-watch.com/en/orient/",
            "https://orient-watch.com/en/orient/collection/contemporary/",
            "https://orient-watch.com/en/orient/collection/classic/",
            "https://orient-watch.com/en/orient/collection/sports/",
            "https://orient-watch.com/en/orient/collection/",
        ],
        "Orient Star": [
            "https://orient-watch.com/en/orientstar/",
            "https://orient-watch.com/en/orientstar/collection/contemporary/",
            "https://orient-watch.com/en/orientstar/collection/contemporary/three-hand/",
            "https://orient-watch.com/en/orientstar/collection/contemporary/lp/three-hand-2026aw/",
            "https://orient-watch.com/en/orientstar/collection/flagship/orientstar-skeleton/",
            "https://orient-watch.com/en/orientstar/collection/classic/",
            "https://orient-watch.com/en/orientstar/collection/sports/",
            "https://orient-watch.com/en/orientstar/collection/",
        ],
    }
    catalogue_cache = {}
    image_owners = {}
    for record in items.values():
        image = record.get("image")
        if image:
            identity = (clean(record.get("brand", "")).lower(), clean(record.get("ref", "")).lower() or clean(record.get("name", "")).lower())
            image_owners.setdefault(image, set()).add(identity)

    def get_soup(url):
        if url not in catalogue_cache:
            try:
                catalogue_cache[url] = page(url)
            except Exception as e:
                print(f"WARN image recovery {url}: {e}")
                catalogue_cache[url] = None
        return catalogue_cache[url]

    for rec in items.values():
        img = rec.get("image")
        duplicate_image = bool(img and len(image_owners.get(img, set())) > 1)
        reference_conflict = bool(img and image_reference_conflicts(img, rec))
        if img and rec.get("image_verified") is True and not image_is_generic(img) and not duplicate_image and not reference_conflict and verify_image_url(img):
            continue
        if reference_conflict:
            print(f"WARN image reference mismatch for {rec.get('ref')}: {img}")
        brand = rec.get("brand")
        ref = clean(rec.get("ref", ""))
        # Prefer images actually discovered on official model pages over guessed URLs.
        product = product_page_for(rec)
        sources = [product, rec.get("source")]
        # For model references, the official brand catalogue is a useful alternate
        # when a product detail endpoint is blocked.
        sources.extend(catalogue_urls.get(brand, []))
        recovered = None
        for source in [x for x in sources if x]:
            soup = get_soup(source)
            if soup is None:
                continue
            # Exact-reference image alt text takes precedence on collection pages.
            found = model_image(soup, source, rec)
            # Announcement records may not have a model reference. Prefer their
            # official social/structured-data image, then a non-generic editorial
            # image from that same official announcement page.
            if not found and not ref:
                found = meta_image(soup, source)
                if not found:
                    for tag in soup.find_all("img"):
                        candidate = image_url_from_tag(tag, source)
                        alt = clean(tag.get("alt", ""))
                        if not candidate or image_is_generic(candidate, alt):
                            continue
                        owners = image_owners.get(candidate, set())
                        ident = (clean(rec.get("brand", "")).lower(), clean(rec.get("ref", "")).lower() or clean(rec.get("name", "")).lower())
                        if owners and owners != {ident}:
                            continue
                        found = candidate
                        break
            if found:
                recovered = found
                if rec.get("ref") and rec["ref"].lower().replace("-", "") in found.lower().replace("-", ""):
                    break
                if source == product:
                    break
        if recovered and not image_is_generic(recovered) and verify_image_url(recovered):
            owners = image_owners.get(recovered, set())
            ident = (clean(rec.get("brand", "")).lower(), clean(rec.get("ref", "")).lower() or clean(rec.get("name", "")).lower())
            if owners and owners != {ident}:
                recovered = None
        if recovered:
            rec["image"] = recovered
            rec["image_verified"] = True
            rec.pop("image_fallback", None)
            rec["image_source"] = "verified official model or release page"
            image_owners.setdefault(recovered, set()).add((clean(rec.get("brand", "")).lower(), clean(rec.get("ref", "")).lower() or clean(rec.get("name", "")).lower()))
        else:
            rec["image"] = None
            rec["image_verified"] = False
            rec["image_fallback"] = True



def enrich_verified_specs(items):
    """Apply a small, explicitly sourced set of recent model specifications.

    Keep regional launch pricing separate: only prices copied from an official
    Singapore product listing are stored in the price field.
    """
    curated = {
        "BY1000-69L": {
            "movement": "Eco-Drive radio-controlled Calibre H874",
            "dial": "Deep blue gradient, forest-inspired pattern and moon-phase display",
            "size": "41.5 mm diameter; 10.8 mm thick",
            "launch_date": "2026-11",
            "edition": "2,400 pieces worldwide",
            "description": "ATTESA EVER SINCE limited edition inspired by a snow-covered forest at dawn. Calibre H874 combines light-powered Eco-Drive, radio-controlled timekeeping and a moon-phase display. The Super Titanium case and bracelet measure 41.5 mm across and 10.8 mm thick; sapphire crystal, 100 m water resistance. Planned global launch: November 2026. Singapore price and allocation are not confirmed.",
            "tags": ["Limited edition", "Eco-Drive", "Radio-controlled", "Moon phase", "2,400 pieces worldwide"]
        },
        "NB6092-01E": {
            "movement": "Automatic Calibre 9054 with GMT",
            "dial": "Black forged carbon",
            "size": "41 mm diameter; 13.5 mm thick",
            "launch_date": "2026-10",
            "edition": "1,300 pieces worldwide",
            "description": "Series 8 fifth-anniversary limited edition combining a Damascus-steel case centre with a forged-carbon dial. Automatic Calibre 9054 offers traveller GMT, approximately 50 hours of power reserve and enhanced magnetic resistance. The 41 mm case is 13.5 mm thick, with anti-reflective sapphire crystal, a transparent case back and 100 m water resistance. Planned global launch: October 2026. Singapore price and allocation are not confirmed.",
            "tags": ["Limited edition", "Series 8", "Automatic GMT", "Damascus steel", "Forged carbon", "1,300 pieces worldwide"]
        },
        "AQ4106-00B": {
            "movement": "Eco-Drive Calibre A060",
            "dial": "White Tosa Unryushi washi paper with gold fibres",
            "size": "38.3 mm diameter; 12.2 mm thick",
            "launch_date": "2026-10",
            "edition": "350 pieces worldwide",
            "description": "The CITIZEN limited edition uses a Tosa Unryushi washi-paper dial with swirling fibres and gold accents, a Sakura Pink Super Titanium case and a crocodile-leather strap. Eco-Drive Calibre A060 offers annual accuracy of ±5 seconds and a perpetual calendar. The case measures 38.3 mm by 12.2 mm, with sapphire crystal and 100 m water resistance. The official Singapore product page lists S$4,578; confirm current availability with Citizen Singapore.",
            "tags": ["Limited edition", "The CITIZEN", "Eco-Drive", "±5 seconds per year", "Washi paper dial", "350 pieces worldwide"]
        }
    }
    for rec in items.values():
        spec = curated.get(clean(rec.get("ref", "")).upper())
        if not spec:
            continue
        for key, value in spec.items():
            if value not in (None, "", []):
                rec[key] = value
        rec["spec_source"] = {
            "BY1000-69L": "https://www.citizenwatch-global.com/news/2026/20261013/index.html",
            "NB6092-01E": "https://www.citizenwatch-global.com/news/2026/20260924/index.html",
            "AQ4106-00B": "https://citizen.com.sg/product/aq4106-00b/"
        }[clean(rec.get("ref", "")).upper()]
        # A Singapore price is attached only to a model with an official SG listing.
        if clean(rec.get("ref", "")).upper() == "AQ4106-00B":
            rec["price"] = 4578.0
            rec["price_currency"] = "SGD"
            rec["price_source"] = "https://citizen.com.sg/product/aq4106-00b/"
        else:
            rec["price"] = None
            rec.pop("price_currency", None)
            rec.pop("price_source", None)

def validate_catalogue(records):
    """Fail the sync when the published catalogue violates core integrity rules."""
    errors = []
    ids = {}
    refs = {}
    images = {}
    for rec in records:
        rid = clean(rec.get("id", ""))
        if not rid:
            errors.append(f"Record has no id: {clean(rec.get('ref', '')) or clean(rec.get('name', ''))}")
        elif rid in ids:
            errors.append(f"Duplicate record id: {rid}")
        else:
            ids[rid] = rec

        ref = clean(rec.get("ref", "")).lower()
        brand = clean(rec.get("brand", "")).lower()
        if brand and ref:
            key = (brand, ref)
            if key in refs:
                errors.append(f"Duplicate model reference: {brand} {ref}")
            else:
                refs[key] = rec

        image = clean(rec.get("image", ""))
        if image and rec.get("image_verified") is True and not rec.get("image_fallback"):
            images.setdefault(image, []).append(rec)
            if image_reference_conflicts(image, rec):
                errors.append(f"Image filename conflicts with model reference: {rec.get('ref')} -> {image}")

        price = rec.get("price")
        if price is not None:
            price_source = clean(rec.get("price_source", ""))
            currency = clean(rec.get("price_currency", ""))
            if not isinstance(price, (int, float)) or price <= 0 or price > 100000:
                errors.append(f"Invalid price for {rec.get('ref')}: {price}")
            if currency != "SGD":
                errors.append(f"Price currency is not explicitly SGD for {rec.get('ref')}: {currency or 'missing'}")
            if not price_source.startswith("https://citizen.com.sg/"):
                errors.append(f"Price lacks an official Singapore Citizen source for {rec.get('ref')}")

        source = clean(rec.get("source", ""))
        if source and not source.startswith("https://"):
            errors.append(f"Non-HTTPS source URL for {rec.get('ref') or rec.get('name')}")

    for image, owners in images.items():
        unique_models = {(clean(x.get("brand", "")).lower(), clean(x.get("ref", "")).lower() or clean(x.get("name", "")).lower()) for x in owners}
        if len(unique_models) > 1:
            errors.append(f"Image URL reused by different models: {image}")

    print(f"CATALOGUE AUDIT: {len(records)} records, {len(ids)} unique ids, "
          f"{len(refs)} unique brand/reference pairs, {len(images)} verified image URLs, "
          f"{sum(1 for r in records if r.get('price') is not None)} priced records")
    if errors:
        for error in errors:
            print(f"ERROR catalogue integrity: {error}")
        raise SystemExit(f"Catalogue integrity audit failed with {len(errors)} issue(s).")


def main():
    items = seed_existing()
    successful = 0
    for fn in (scrape_citizen_archive, scrape_orient_archive, scrape_orient_catalogues, scrape_citizen_singapore):
        before = len(items)
        try:
            fn(items)
            successful += 1
            print(f"OK {fn.__name__}: {len(items)-before} new/updated records")
        except Exception as e:
            print(f"WARN {fn.__name__}: {e}")
    enrich_verified_specs(items)
    repair_missing_images(items)
    # Validate scraped records and collapse the same model discovered from multiple links.
    # Broad Orient announcement summaries are not individual watches. Keep the watch
    # catalogue focused on identifiable Orient / Orient Star model references.
    cleaned = {}
    for rec in items.values():
        if rec.get("brand") in {"Orient", "Orient Star"} and not clean(rec.get("ref", "")):
            continue
        if not valid_watch_title(rec.get("name", "")):
            continue
        rec["name"] = strip_release_date(rec.get("name", ""))
        if rec.get("image") and image_is_generic(rec["image"]):
            rec["image"] = None
            rec["image_verified"] = False
            rec["image_fallback"] = True
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
            old_verified_image = old.get("image") if old.get("image_verified") is True else None
            new_verified_image = rec.get("image") if rec.get("image_verified") is True else None
            old_headline = len(old_name) > 95 or bool(re.match(r"^\d{1,2}\s", old_name))
            new_headline = len(new_name) > 95 or bool(re.match(r"^\d{1,2}\s", new_name))
            if (old_headline and not new_headline) or (not old.get("image") and rec.get("image")) or (old.get("image_fallback") and rec.get("image") and not rec.get("image_fallback")):
                merged = dict(old)
                merged.update({k: v for k, v in rec.items() if v not in (None, "", [])})
                if not new_headline:
                    merged["name"] = new_name
                if new_verified_image:
                    merged["image"] = new_verified_image
                    merged["image_verified"] = True
                    merged.pop("image_fallback", None)
                    if rec.get("image_source"):
                        merged["image_source"] = rec["image_source"]
                elif old_verified_image:
                    merged["image"] = old_verified_image
                    merged["image_verified"] = True
                    merged.pop("image_fallback", None)
                cleaned[key] = merged
            else:
                for field in ("image", "price", "ref", "date"):
                    if not old.get(field) and rec.get(field):
                        old[field] = rec[field]
    for rec in cleaned.values():
        if (not rec.get("image") or rec.get("image_verified") is not True or image_is_generic(rec.get("image", "")) or image_reference_conflicts(rec.get("image", ""), rec)):
            rec["image"] = None
            rec["image_verified"] = False
            rec["image_fallback"] = True
    records = sorted(cleaned.values(), key=lambda x: (x.get("date") or "", x.get("brand", ""), x.get("name", "")), reverse=True)
    validate_catalogue(records)
    data = {"last_checked_at": NOW, "source_count": successful, "data_mode": "scheduled-source-sync", "releases": records}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(records)} official-source records to data/releases.json")
    if successful == 0:
        raise SystemExit("All source checks failed; retaining only previously saved records. See warnings above.")

if __name__ == "__main__":
    main()

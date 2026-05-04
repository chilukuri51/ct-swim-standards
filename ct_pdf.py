"""CT Swim meet result PDFs — index scraper, PDF parser, swimmer lookup.

Replaces the swimstandards.com triangulation source. Works entirely from
public CT Swim data: the meet results index at ctswim.org/Meets/Results.aspx
and the per-meet PDF artifacts.

Three responsibilities:
1. Scrape Results.aspx pages → list of (pdf_url, label) entries.
2. Parse a single Hy-Tek-format result PDF → list of swimmer rows.
3. Match a meet (name + date range) to its PDF in the index.
"""

import io
import re
from datetime import date
from typing import Optional

import requests
from pypdf import PdfReader
from bs4 import BeautifulSoup


CT_SWIM_BASE = 'https://www.ctswim.org'
RESULTS_INDEX = f'{CT_SWIM_BASE}/Meets/Results.aspx'
INDEX_MAX_PAGES = 50  # generous upper bound; loop stops on first empty page

USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)


def normalize_name(first: str, last: str) -> str:
    """Canonical key for matching across CT Swim and PDF formats.
    'Saanvi' + 'Chilukuri' → 'chilukuri_saanvi'.
    Strips punctuation, lowercases, removes whitespace within names."""
    f = re.sub(r"[^a-z]", "", (first or '').lower())
    l = re.sub(r"[^a-z]", "", (last or '').lower())
    return f"{l}_{f}"


# ===== Index scraping =====

_PDF_LINK_RE = re.compile(
    r'href="([^"]*_results\.pdf)"[^>]*>([^<]*)<', re.IGNORECASE
)


def scrape_results_index(max_pages: int = INDEX_MAX_PAGES) -> list[dict]:
    """Scrape every page of CT Swim's Results.aspx for `_results.pdf` links.
    Returns list of {url, label}. Stops early when a page yields zero links.
    """
    out = []
    seen_urls = set()
    for p in range(1, max_pages + 1):
        try:
            r = requests.get(
                f'{RESULTS_INDEX}?page={p}',
                timeout=15,
                headers={'User-Agent': USER_AGENT}
            )
            if r.status_code != 200:
                break
        except Exception:
            break
        page_added = 0
        for m in _PDF_LINK_RE.finditer(r.text):
            url = m.group(1).strip()
            if url in seen_urls:
                continue
            seen_urls.add(url)
            out.append({'url': url, 'label': m.group(2).strip()})
            page_added += 1
        if page_added == 0:
            break
    return out


# ===== Meet → PDF matching =====

_DATE_RE = re.compile(r'(\d{1,2})/(\d{1,2})/(\d{4})')


def parse_us_date(s: str) -> Optional[date]:
    if not s:
        return None
    m = _DATE_RE.search(s)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _name_keys(meet_name: str) -> set:
    """Extract distinctive lowercase keywords from a meet name. Drops dates,
    common stop-words ('regional', 'champs', etc. STAY since they're useful)
    and weekday abbreviations."""
    drop = {'and', 'the', 'for', 'sat', 'sun', 'sat-sun', 'sat-s',
            'fri-sun', 'fri', 'tues', 'wed', 'thur', 'mon', 'open',
            'invitational', 'meet', 'swim', 'swimming', 'ct'}
    words = re.findall(r'[A-Za-z]{3,}', (meet_name or '').lower())
    return {w for w in words if w not in drop}


def find_pdf_for_meet(
    meet_name: str,
    start_date: Optional[date],
    end_date: Optional[date],
    index: list[dict],
    min_score: int = 8,
) -> Optional[dict]:
    """Score each indexed PDF against a known meet and return the best match.

    Scoring:
      +10 if URL contains MMDDYY of meet start date
      +10 if URL contains MMDDYY of meet end date
       +2 per distinctive name keyword matched in URL or label
    Returns None if no PDF scores at least `min_score` — better to skip than
    record a wrong match.
    """
    if not index:
        return None
    keys = _name_keys(meet_name)
    target_dates = []
    if start_date:
        target_dates.append(start_date.strftime('%m%d%y'))
    if end_date and end_date != start_date:
        target_dates.append(end_date.strftime('%m%d%y'))

    best = None
    best_score = -1
    for p in index:
        haystack = (p['url'] + ' ' + p['label']).lower()
        score = 0
        for d in target_dates:
            if d in p['url']:
                score += 10
        score += sum(2 for k in keys if k in haystack)
        if score > best_score:
            best_score = score
            best = p
    if best_score < min_score:
        return None
    return best


# ===== PDF parsing =====

# pypdf extracts Hy-Tek result rows in this order (different from how the
# columns are laid out visually): "TEAM-CT  AGE Last, First<rank> Time"
# Examples actually seen on CT meet PDFs:
#   "IVY -CT  9Chilukuri, Saanvi12 41.18"
#   "HHAC-CT 10Destefano, Mackenzie1 36.12"
# Note: short team codes get padded with a space before -CT ("IVY -CT").
# Age is jammed up against last name; rank is jammed up against first name.
# pdfplumber preserved the visual column order but uses ~4x more memory,
# so we accept this layout and shape the regex to match it.
_PDF_ROW_RE = re.compile(
    r"\b([A-Z]{2,6})\s*-CT\s+"                                # team
    r"(\d{1,2})"                                              # age (jammed)
    r"([A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+)*),\s+"       # last
    r"([A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+)*)\d"         # first + start of rank
)
_GENDER_HEADER_RE = re.compile(
    r"\b(Girls|Boys|Women|Men|Mixed)\s+\d", re.IGNORECASE
)


def parse_results_pdf(path_or_bytes) -> list[dict]:
    """Parse one Hy-Tek result PDF using pypdf (lighter than pdfplumber:
    ~37 MB peak vs ~154 MB, important on Render Starter's 512 MB cap).

    Returns one dict per swimmer-event row:
      {first, last, name_key, age, team, gender}

    Same swimmer appears once per event in the meet — callers that want
    one row per swimmer should dedupe by name_key (the lookup helper
    `lookup_swimmer_age_at_meet` already returns a single row).
    """
    out = []
    if isinstance(path_or_bytes, (bytes, bytearray)):
        reader = PdfReader(io.BytesIO(path_or_bytes))
    else:
        reader = PdfReader(path_or_bytes)

    current_gender = ''
    for page in reader.pages:
        text = page.extract_text() or ''
        for line in text.split('\n'):
            g = _GENDER_HEADER_RE.search(line)
            if g:
                current_gender = g.group(1)[0].upper()
            for m in _PDF_ROW_RE.finditer(line):
                team, age, last, first = m.groups()
                out.append({
                    'first': first,
                    'last': last,
                    'name_key': normalize_name(first, last),
                    'age': int(age),
                    'team': team,
                    'gender': current_gender,
                })
    return out


def download_pdf(url: str) -> Optional[bytes]:
    """Fetch a PDF from CT Swim. Relative URLs are resolved against the base."""
    if not url:
        return None
    full = url if url.startswith('http') else CT_SWIM_BASE + url
    try:
        r = requests.get(full, timeout=30, headers={'User-Agent': USER_AGENT})
        if r.status_code != 200:
            return None
        return r.content
    except Exception:
        return None


# ===== SwimmerAtMeet metadata =====

CT_FAST_BASE = 'https://fast.ctswim.org/CTNet'

# Label4 on SwimmerAtMeet has format like:
#   '2025 CT LEHY 12U Regional Sat-SunEast Hartford, CT7/19/2025 - 7/20/2025'
# Three logical parts concatenated without separators: meet_name, location,
# date range. We extract from the right: dates first, then strip them.
_DATE_RANGE_RE = re.compile(
    r'(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})'
)
_SINGLE_DATE_RE = re.compile(r'(\d{1,2}/\d{1,2}/\d{4})')


def fetch_swimmer_at_meet(swimmer_ct_id: str, ct_meet_id: str) -> Optional[dict]:
    """Hit SwimmerAtMeet.aspx → return {meet_name, start_date, end_date}.
    Dates are date objects; meet_name is the cleaned event title.

    Uses BeautifulSoup so nested HTML inside Label4 (br/span/etc.) is
    handled correctly. The label has three logical parts concatenated:
      meet_name + location + date_range
    e.g. '2025 CT LEHY 12U Regional Sat-SunEast Hartford, CT7/19/2025 - 7/20/2025'
    We pull the date range first, then strip the trailing 'City, ST' as
    location, and what remains is the meet name.
    """
    if not swimmer_ct_id or not ct_meet_id:
        return None
    url = f'{CT_FAST_BASE}/SwimmerAtMeet.aspx?id={swimmer_ct_id}&m={ct_meet_id}'
    try:
        r = requests.get(url, timeout=15, headers={'User-Agent': USER_AGENT})
        if r.status_code != 200:
            return None
    except Exception:
        return None

    soup = BeautifulSoup(r.text, 'html.parser')
    # Try Label4 first, fall back to Label3 if absent (page layout varies
    # for some older meets).
    label_text = ''
    for label_id in ('Label4', 'Label3'):
        el = soup.find(id=label_id)
        if not el:
            continue
        # get_text with a separator so "Sat-Sun<br>City" doesn't collapse
        # into "Sat-SunCity"; we'll re-collapse whitespace below.
        text = el.get_text(separator=' ', strip=True)
        text = re.sub(r'\s+', ' ', text).strip()
        if text and any(c.isdigit() for c in text):
            label_text = text
            break
    if not label_text:
        return None

    # Pull dates first (range or single). meet_name keeps the full pre-date
    # text including any "City, ST" location — extra keywords don't hurt
    # the matcher, and trying to strip them by regex was producing wrong
    # cuts (e.g. "2023 CT NCA Fall Invite" had its first "CT" mistaken
    # for a state code).
    drm = _DATE_RANGE_RE.search(label_text)
    if drm:
        start = parse_us_date(drm.group(1))
        end = parse_us_date(drm.group(2))
        name = label_text[:drm.start()].strip()
    else:
        sdm = _SINGLE_DATE_RE.search(label_text)
        if not sdm:
            return {
                'meet_name': label_text.strip(),
                'start_date': None,
                'end_date': None,
            }
        start = parse_us_date(sdm.group(1))
        end = start
        name = label_text[:sdm.start()].strip()

    return {
        'meet_name': name,
        'start_date': start,
        'end_date': end,
    }

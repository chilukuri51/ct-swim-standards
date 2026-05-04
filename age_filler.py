"""Background job that triangulates team_member ages from CT Swim meet PDFs.

For each linked team_member: walk their best-time events, follow event-history
to collect (date, ct_meet_id) tuples, look up each meet's result PDF, find
this swimmer in the parsed PDF, then apply the championship age-up rule
(age = age on first day of meet) to triangulate birth_year/birth_month.

Replaces the swimstandards.com fetcher (Cloudflare-blocked from Render IPs).
Uses only public CT Swim data — works fully on Render with no proxy.

Match swimmers by name (not strict team) so a club change like Simon Allegra
FVYT → IVY still finds his historical FVYT swims.
"""

import threading
import time
import random
import re
from dataclasses import dataclass, asdict, field
from typing import Optional
from datetime import datetime, date, timedelta, timezone

import requests
from bs4 import BeautifulSoup

import db
import ct_pdf


# Polite delays between CT Swim requests inside the triangulation loop.
DELAY_MIN = 2
DELAY_MAX = 5

# Cache TTL: skip if synced within this many days, unless still missing data.
CACHE_DAYS = 90
NEAR_BIRTHDAY_WINDOW = 60

# CT Swim "fast" host (per-swimmer pages live here) and the "main" site
# (results PDF index lives there).
CT_FAST_BASE = 'https://fast.ctswim.org/CTNet'

USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_delay() -> float:
    return random.uniform(DELAY_MIN, DELAY_MAX)


@dataclass
class FillerState:
    running: bool = False
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    cancelled: bool = False
    total: int = 0
    done: int = 0
    current: str = ''
    updated: int = 0           # birth_year and/or birth_month set
    observed_only: int = 0     # found page but no usable triangulation
    not_found: int = 0         # no meets / nothing in PDFs
    skipped: int = 0
    gender_filled: int = 0
    fully_resolved: int = 0    # already locked in, skipped by selection
    pdfs_parsed: int = 0       # NEW: meet PDFs parsed during this run
    errors: list = field(default_factory=list)

    def progress_pct(self) -> int:
        if self.total == 0:
            return 0
        return int((self.done / self.total) * 100)

    def eta_seconds(self) -> int:
        avg = (DELAY_MIN + DELAY_MAX) / 2
        # Each member triggers ~5 fetches on average (events + meets).
        return int(max(0, self.total - self.done) * avg * 5)


_state = FillerState()
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()
_index_cache = {'rows': [], 'fetched_at': None}


def get_status() -> dict:
    with _lock:
        d = asdict(_state)
    d['progress_pct'] = _state.progress_pct()
    d['eta_seconds'] = _state.eta_seconds()
    return d


def cancel_job():
    with _lock:
        _state.cancelled = True


# ===== Triangulation math (unchanged from swimstandards version) =====

def triangulate(records: list, today: Optional[date] = None) -> Optional[dict]:
    """records: list of {'age': int, 'meet_date': date}. Apply the
    age-up = first day of meet rule and intersect birth windows."""
    today = today or date.today()
    lo = date(1990, 1, 1)
    hi = today
    n = 0
    for rec in records:
        age = rec.get('age')
        d = rec.get('meet_date')
        if age is None or d is None:
            continue
        try:
            rec_lo = d.replace(year=d.year - age - 1) + timedelta(days=1)
        except ValueError:
            rec_lo = date(d.year - age - 1, 3, 1)
        try:
            rec_hi = d.replace(year=d.year - age)
        except ValueError:
            rec_hi = date(d.year - age, 2, 28)
        lo = max(lo, rec_lo)
        hi = min(hi, rec_hi)
        n += 1
        if lo > hi:
            return None
    if n == 0:
        return None
    span = (hi - lo).days
    same_year = (lo.year == hi.year)
    same_month = same_year and (lo.month == hi.month)
    return {
        'birth_year': lo.year if same_year else None,
        'birth_month': lo.month if same_month else None,
        'window_days': span,
        'samples': n,
        'lo': lo.isoformat(),
        'hi': hi.isoformat(),
    }


# ===== CT Swim page fetchers =====

def _http_get(url: str, timeout: int = 15) -> Optional[str]:
    try:
        r = requests.get(url, timeout=timeout, headers={'User-Agent': USER_AGENT})
        if r.status_code != 200:
            return None
        return r.text
    except Exception:
        return None


def _fetch_event_history_meets(history_url: str) -> list:
    """Fetch one event-history page and extract (date, ct_meet_id) tuples
    from each row's [Meet] link. The cached event_history table is
    refreshed by app.ct_fetch_event_history during regular fetches; we
    parse fresh here too in case caches are stale."""
    if not history_url:
        return []
    html = _http_get(f"{CT_FAST_BASE}/{history_url}")
    if not html:
        return []
    soup = BeautifulSoup(html, 'html.parser')
    out = []
    for tbl in soup.find_all('table'):
        rows = tbl.find_all('tr')
        if not rows:
            continue
        hdr = [c.get_text(strip=True) for c in rows[0].find_all(['td', 'th'])]
        if hdr[:3] != ['Time', 'Swim', 'Date']:
            continue
        for row in rows[1:]:
            cells = row.find_all('td')
            if len(cells) < 4:
                continue
            date_str = cells[2].get_text(strip=True)
            link = cells[3].find('a', href=True)
            if not link:
                continue
            m = re.search(r'm=(\d+)', link['href'])
            if m:
                out.append({'date': date_str, 'ct_meet_id': m.group(1)})
        break
    return out


def _gather_meet_history(member: dict) -> list:
    """Collect all unique (ct_meet_id, date) tuples for this swimmer.
    Reads cached event_history first; falls back to live fetches for any
    missing event histories."""
    out = {}  # ct_meet_id -> date_str (keep latest seen)
    # Cached path first (fast): use any (ct_meet_id, date) already in DB.
    for r in db.get_member_meet_history(member['id']):
        if r['ct_meet_id']:
            out[r['ct_meet_id']] = r['date']

    # Walk best-times to find events whose history we haven't fetched yet.
    ct_id = member.get('ct_id')
    if not ct_id:
        return [{'ct_meet_id': mid, 'date': dstr} for mid, dstr in out.items()]
    bt = db.get_best_times(ct_id)
    if not bt:
        return [{'ct_meet_id': mid, 'date': dstr} for mid, dstr in out.items()]

    for ev in bt.get('events', []):
        hu = ev.get('history_url')
        if not hu:
            continue
        cached = db.get_event_history(hu)
        if cached:
            for h in cached:
                mid = h.get('ct_meet_id')
                if mid:
                    out.setdefault(mid, h.get('date', ''))
            continue
        # Not cached — fetch
        rows = _fetch_event_history_meets(hu)
        # Persist what we found so future runs are fast.
        if rows:
            try:
                db.save_event_history(
                    hu, ct_id, ev.get('event', ''),
                    [{'time': '', 'meet': '', 'date': r['date'],
                      'ct_meet_id': r['ct_meet_id']} for r in rows]
                )
            except Exception:
                pass
        for r in rows:
            out.setdefault(r['ct_meet_id'], r['date'])
        time.sleep(_next_delay())

    return [{'ct_meet_id': mid, 'date': dstr} for mid, dstr in out.items()]


# ===== Per-meet PDF resolution =====

def _ensure_index_cached() -> list:
    """Scrape Results.aspx once per run (~5s). Cached in module-level dict."""
    if _index_cache['rows']:
        return _index_cache['rows']
    rows = ct_pdf.scrape_results_index()
    _index_cache['rows'] = rows
    _index_cache['fetched_at'] = _now_iso()
    return rows


def _resolve_meet_pdf(swimmer_ct_id: str, ct_meet_id: str) -> Optional[dict]:
    """Ensure meet_pdf_cache + meet_pdf_swimmers are populated for this meet.
    Returns the cache row dict, or None if no PDF available.
    """
    cached = db.get_meet_cache(ct_meet_id)
    if cached and cached.get('parsed_at'):
        return cached
    if cached and cached.get('note') == 'no_pdf':
        return cached  # already determined unfindable

    # Step 1: get meet name + dates from CT Swim
    meta = ct_pdf.fetch_swimmer_at_meet(swimmer_ct_id, ct_meet_id)
    if not meta:
        db.save_meet_cache(ct_meet_id, note='no_meta')
        return None

    db.save_meet_cache(
        ct_meet_id,
        meet_name=meta.get('meet_name'),
        start_date=meta['start_date'].isoformat() if meta.get('start_date') else None,
        end_date=meta['end_date'].isoformat() if meta.get('end_date') else None,
    )

    # Step 2: find the PDF in the index
    index = _ensure_index_cached()
    pdf = ct_pdf.find_pdf_for_meet(
        meta.get('meet_name', ''),
        meta.get('start_date'),
        meta.get('end_date'),
        index,
    )
    if not pdf:
        db.save_meet_cache(ct_meet_id, note='no_pdf')
        return db.get_meet_cache(ct_meet_id)

    # Step 3: download + parse PDF
    body = ct_pdf.download_pdf(pdf['url'])
    if not body:
        db.save_meet_cache(ct_meet_id, pdf_url=pdf['url'], note='download_failed')
        return db.get_meet_cache(ct_meet_id)

    try:
        rows = ct_pdf.parse_results_pdf(body)
    except Exception as e:
        db.save_meet_cache(ct_meet_id, pdf_url=pdf['url'],
                           note=f'parse_error:{type(e).__name__}')
        return db.get_meet_cache(ct_meet_id)

    # Step 4: save parsed swimmers + mark cache complete
    db.save_meet_pdf_swimmers(ct_meet_id, rows)
    db.save_meet_cache(ct_meet_id,
                       pdf_url=pdf['url'],
                       parsed_at=_now_iso(),
                       note=None)
    with _lock:
        _state.pdfs_parsed += 1
    return db.get_meet_cache(ct_meet_id)


# ===== Per-member triangulation =====

def _process_member(member: dict, force_all: bool = False) -> dict:
    """Walk one swimmer's meets, look up age in each parsed PDF, triangulate.
    Returns a status tag dict the runner records into FillerState."""
    name_key = ct_pdf.normalize_name(
        member.get('first_name', ''), member.get('last_name', '')
    )

    meets = _gather_meet_history(member)
    if not meets:
        # No meet history at all — likely a brand-new linked swimmer or
        # one whose CT Swim cache hasn't been batch-fetched yet.
        db.save_member_triangulation(member['id'])
        return {'status': 'not_found'}

    records = []
    observed_age = None
    observed_gender = None
    for m in meets:
        if not m.get('ct_meet_id'):
            continue
        meet_date = ct_pdf.parse_us_date(m.get('date', ''))
        if not meet_date:
            continue
        cache = _resolve_meet_pdf(member['ct_id'], m['ct_meet_id'])
        if not cache or not cache.get('parsed_at'):
            continue
        hit = db.lookup_swimmer_age_at_meet(m['ct_meet_id'], name_key)
        if not hit:
            continue
        records.append({'age': hit['age'], 'meet_date': meet_date})
        # Track the most recent observation for age_observed + gender backfill
        if observed_age is None or meet_date > _record_date_max(records[:-1]):
            observed_age = hit['age']
            observed_gender = hit.get('gender')
        time.sleep(_next_delay() / 4)  # tiny inter-meet pause

    if not records:
        db.save_member_triangulation(member['id'])
        return {'status': 'not_found'}

    # Gender: fill if missing (or force_all on a re-verify pass).
    gender_filled = False
    if observed_gender and (force_all or not member.get('gender')):
        if observed_gender != member.get('gender'):
            db.update_team_member(member['id'], gender=observed_gender)
            gender_filled = True

    tri = triangulate(records)
    if tri is None:
        db.save_member_triangulation(member['id'], age_observed=observed_age)
        return {
            'status': 'observed_only' if observed_age is not None else 'skipped',
            'gender_filled': gender_filled,
        }

    db.save_member_triangulation(
        member['id'],
        birth_year=tri.get('birth_year'),
        birth_month=tri.get('birth_month'),
        window_days=tri.get('window_days'),
        age_observed=observed_age,
    )
    return {
        'status': 'updated',
        'tri': tri,
        'narrowed_year': tri.get('birth_year') is not None,
        'narrowed_month': tri.get('birth_month') is not None,
        'gender_filled': gender_filled,
        'samples': tri.get('samples'),
    }


def _record_date_max(records):
    if not records:
        return date(1900, 1, 1)
    return max(r['meet_date'] for r in records)


# ===== Selection logic + job orchestration =====

def _is_near_known_birthday(member: dict, window_days: int = NEAR_BIRTHDAY_WINDOW) -> bool:
    bm = member.get('birth_month')
    if not bm:
        return False
    today = date.today()
    try:
        anchor = date(today.year, int(bm), 1)
    except ValueError:
        return False
    if abs((today - anchor).days) <= window_days:
        return True
    try:
        anchor_prev = date(today.year - 1, int(bm), 1)
        anchor_next = date(today.year + 1, int(bm), 1)
    except ValueError:
        return False
    return min(abs((today - anchor_prev).days),
               abs((today - anchor_next).days)) <= window_days


def _select_candidates(members: list, force_all: bool) -> list:
    """Same selection rules as before: skip fully-resolved (year+month set)
    unless force_all; always retry when birth_year is unknown; honor 90-day
    cache for partials except near a known birthday."""
    cutoff = (date.today() - timedelta(days=CACHE_DAYS)).isoformat()
    out = []
    for m in members:
        if not m.get('ct_id'):
            continue
        if force_all:
            out.append(m)
            continue
        if m.get('birth_year') and m.get('birth_month'):
            continue
        if not m.get('birth_year'):
            out.append(m)
            continue
        synced = m.get('age_synced_at') or ''
        if not synced:
            out.append(m)
            continue
        if synced[:10] < cutoff:
            out.append(m)
            continue
        if _is_near_known_birthday(m):
            out.append(m)
    return out


def start_autofill(force_all: bool = False) -> bool:
    """Start the autofill job. Returns False if a job is already running."""
    global _thread, _state, _index_cache
    if _thread and _thread.is_alive():
        return False

    # Reset per-run cache so we re-scrape the index on each new job (it
    # changes when new meets get published).
    _index_cache = {'rows': [], 'fetched_at': None}

    members = db.list_team_members(include_birth=True)
    candidates = _select_candidates(members, force_all)
    fully_resolved = sum(
        1 for m in members
        if m.get('ct_id') and m.get('birth_year') and m.get('birth_month')
    )

    _state = FillerState(
        running=True,
        started_at=_now_iso(),
        total=len(candidates),
        fully_resolved=fully_resolved,
    )

    if not candidates:
        with _lock:
            _state.running = False
            _state.finished_at = _now_iso()
        return True

    _thread = threading.Thread(
        target=_run, args=(candidates, force_all), daemon=True
    )
    _thread.start()
    return True


def _sleep_respecting_cancel(seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        with _lock:
            if _state.cancelled:
                return False
        time.sleep(min(0.5, end - time.time()))
    return True


def _run(candidates, force_all: bool = False):
    try:
        for i, m in enumerate(candidates):
            with _lock:
                if _state.cancelled:
                    break
                _state.current = f"{m['first_name']} {m['last_name']}"

            try:
                result = _process_member(m, force_all=force_all)
                with _lock:
                    s = result.get('status')
                    if s == 'updated':
                        _state.updated += 1
                    elif s == 'not_found':
                        _state.not_found += 1
                    elif s == 'observed_only':
                        _state.observed_only += 1
                    else:
                        _state.skipped += 1
                    if result.get('gender_filled'):
                        _state.gender_filled += 1

                samples = result.get('samples', 0)
                tag = s
                if s == 'updated':
                    tag = f"updated/{samples}smp"
                db.log_sync(
                    m.get('ct_id'), 'age_fill_pdf',
                    'ok' if s == 'updated' else s,
                    f"{m['first_name']} {m['last_name']}: {tag}"
                    + (' +gender' if result.get('gender_filled') else '')
                )
            except Exception as e:
                with _lock:
                    _state.errors.append(f"{m['first_name']} {m['last_name']}: {e}")
                db.log_sync(
                    m.get('ct_id'), 'age_fill_pdf', 'error',
                    f"{m['first_name']} {m['last_name']}: {e}"
                )

            with _lock:
                _state.done = i + 1

            if i < len(candidates) - 1:
                if not _sleep_respecting_cancel(_next_delay()):
                    break
    finally:
        with _lock:
            _state.running = False
            _state.finished_at = _now_iso()

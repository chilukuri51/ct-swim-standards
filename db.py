"""SQLite storage for SwimProgression.

Single file DB at swimprogression.db in the project root.
No ORM - just sqlite3 for simplicity.
"""

import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime, timezone

import paths


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

DB_PATH = paths.DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS team_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    roster TEXT,
    gender TEXT,
    dob TEXT,                    -- legacy; superseded by birth_year/month
    birth_year INTEGER,          -- coach-entered, privacy-friendly
    birth_month INTEGER,         -- 1-12
    age_observed INTEGER,        -- last age scraped (CT Swim or swimstandards)
    age_observed_at TEXT,        -- ISO date of that observation
    age_synced_at TEXT,          -- last successful swimstandards triangulation
    age_window_days INTEGER,     -- size of birth-window when triangulated
    parent_email TEXT,           -- lowercase email; one or more kids share an email
    ct_id TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_team_members_name ON team_members(last_name, first_name);
CREATE INDEX IF NOT EXISTS idx_team_members_ctid ON team_members(ct_id);

CREATE TABLE IF NOT EXISTS swimmers (
    ct_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    team_code TEXT,
    last_name_lower TEXT,
    last_synced TEXT,
    roster TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_swimmers_last_name ON swimmers(last_name_lower);
CREATE INDEX IF NOT EXISTS idx_swimmers_team ON swimmers(team_code);

CREATE TABLE IF NOT EXISTS best_times (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    swimmer_ct_id TEXT NOT NULL,
    event TEXT NOT NULL,
    time TEXT NOT NULL,
    date TEXT,
    history_url TEXT,
    UNIQUE(swimmer_ct_id, event),
    FOREIGN KEY (swimmer_ct_id) REFERENCES swimmers(ct_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_best_times_swimmer ON best_times(swimmer_ct_id);

CREATE TABLE IF NOT EXISTS event_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    swimmer_ct_id TEXT NOT NULL,
    event TEXT NOT NULL,
    history_url TEXT NOT NULL,
    time TEXT NOT NULL,
    swim_type TEXT,
    date TEXT,
    ct_meet_id TEXT,                        -- m= param on SwimmerAtMeet.aspx
    FOREIGN KEY (swimmer_ct_id) REFERENCES swimmers(ct_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_event_history_url ON event_history(history_url);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    swimmer_ct_id TEXT,
    action TEXT,
    status TEXT,
    message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- CT Swim meet PDF cache. Populated lazily as we triangulate ages.
-- ct_meet_id is the `m` parameter on SwimmerAtMeet.aspx (string of digits).
CREATE TABLE IF NOT EXISTS meet_pdf_cache (
    ct_meet_id TEXT PRIMARY KEY,
    meet_name TEXT,
    start_date TEXT,            -- ISO YYYY-MM-DD
    end_date TEXT,              -- ISO YYYY-MM-DD
    pdf_url TEXT,               -- path or absolute URL; NULL if no PDF found
    parsed_at TEXT,             -- ISO timestamp when PDF was parsed (NULL = not yet)
    note TEXT                   -- 'no_pdf' | 'parse_error' | NULL
);

-- One row per swimmer per meet (deduped from possibly many event rows).
CREATE TABLE IF NOT EXISTS meet_pdf_swimmers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ct_meet_id TEXT NOT NULL,
    name_key TEXT NOT NULL,     -- 'lastname_firstname' lowercase
    first_name TEXT,
    last_name TEXT,
    age INTEGER NOT NULL,
    gender TEXT,
    team TEXT,
    UNIQUE(ct_meet_id, name_key, age),
    FOREIGN KEY (ct_meet_id) REFERENCES meet_pdf_cache(ct_meet_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_meet_pdf_sw_lookup
    ON meet_pdf_swimmers(name_key, ct_meet_id);
"""


@contextmanager
def get_conn():
    # timeout=30 lets concurrent writers wait up to 30s for the write
    # lock instead of immediately raising "database is locked". Combined
    # with WAL mode (set in init_db), this lets the 4-wide age_filler
    # thread pool save concurrently without dropping rows.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        # WAL mode: allow concurrent reads + serialized writes without
        # raising "database is locked" under thread contention. Persists
        # across connections once set.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        # Add roster column to existing DBs (no-op if it already exists)
        try:
            conn.execute("ALTER TABLE swimmers ADD COLUMN roster TEXT")
        except sqlite3.OperationalError:
            pass
        # Add new privacy-friendly age columns to existing team_members
        for col, typ in (
            ('birth_year', 'INTEGER'),
            ('birth_month', 'INTEGER'),
            ('age_observed', 'INTEGER'),
            ('age_observed_at', 'TEXT'),
            ('age_synced_at', 'TEXT'),
            ('age_window_days', 'INTEGER'),
            ('parent_email', 'TEXT'),
        ):
            try:
                conn.execute(f"ALTER TABLE team_members ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass
        # Add ct_meet_id to existing event_history (Phase: CT PDF triangulation)
        try:
            conn.execute("ALTER TABLE event_history ADD COLUMN ct_meet_id TEXT")
        except sqlite3.OperationalError:
            pass
        # One-time migration: backfill birth_year/month from any existing dob values.
        conn.execute("""
            UPDATE team_members
            SET birth_year  = CAST(substr(dob, 1, 4) AS INTEGER),
                birth_month = CAST(substr(dob, 6, 2) AS INTEGER)
            WHERE dob IS NOT NULL AND dob != ''
              AND birth_year IS NULL
        """)
        # Erase the legacy dob string after migration to honor the privacy goal.
        conn.execute("""
            UPDATE team_members SET dob = NULL
            WHERE dob IS NOT NULL AND birth_year IS NOT NULL
        """)


# ===== Swimmer search / lookup =====

def search_swimmers_by_last_name(last_name):
    """Return cached swimmers whose last name starts with the given prefix (case-insensitive)."""
    prefix = last_name.strip().lower()
    if not prefix:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ct_id, name, team_code FROM swimmers WHERE last_name_lower LIKE ? ORDER BY name",
            (f"{prefix}%",)
        ).fetchall()
    return [{'id': r['ct_id'], 'name': r['name'], 'team': r['team_code'] or ''} for r in rows]


def upsert_swimmer(ct_id, name, team_code):
    last_name = name.strip().split()[-1].lower() if name else ''
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO swimmers (ct_id, name, team_code, last_name_lower, last_synced)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(ct_id) DO UPDATE SET
                name = excluded.name,
                team_code = excluded.team_code,
                last_name_lower = excluded.last_name_lower,
                last_synced = excluded.last_synced
        """, (ct_id, name, team_code, last_name, _utcnow_iso()))


def upsert_swimmers_bulk(swimmers):
    """Bulk upsert without marking last_synced (that only happens on best_times fetch)."""
    if not swimmers:
        return
    with get_conn() as conn:
        for sw in swimmers:
            last_name = sw['name'].strip().split()[-1].lower() if sw.get('name') else ''
            conn.execute("""
                INSERT INTO swimmers (ct_id, name, team_code, last_name_lower)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(ct_id) DO UPDATE SET
                    name = excluded.name,
                    team_code = excluded.team_code,
                    last_name_lower = excluded.last_name_lower
            """, (sw['id'], sw['name'], sw.get('team', ''), last_name))


# ===== Best times =====

def get_best_times(ct_id):
    with get_conn() as conn:
        swimmer = conn.execute(
            "SELECT name, team_code, last_synced FROM swimmers WHERE ct_id = ?", (ct_id,)
        ).fetchone()
        if not swimmer or not swimmer['last_synced']:
            return None
        events = conn.execute(
            "SELECT event, time, date, history_url FROM best_times WHERE swimmer_ct_id = ? ORDER BY id",
            (ct_id,)
        ).fetchall()
    display_name = f"{swimmer['name']} ({swimmer['team_code']})" if swimmer['team_code'] else swimmer['name']
    return {
        'swimmer_name': display_name,
        'events': [dict(e) for e in events],
        'last_synced': swimmer['last_synced'],
        'cached': True,
    }


def save_best_times(ct_id, swimmer_name, events):
    with get_conn() as conn:
        if swimmer_name:
            # swimmer_name from CT Swim looks like "First Last (TEAM)" - keep as-is
            conn.execute(
                "UPDATE swimmers SET name = COALESCE(?, name), last_synced = ? WHERE ct_id = ?",
                (_extract_name_only(swimmer_name), _utcnow_iso(), ct_id)
            )
        # Wipe old times and rewrite (simpler than reconciling)
        conn.execute("DELETE FROM best_times WHERE swimmer_ct_id = ?", (ct_id,))
        for ev in events:
            conn.execute("""
                INSERT INTO best_times (swimmer_ct_id, event, time, date, history_url)
                VALUES (?, ?, ?, ?, ?)
            """, (ct_id, ev.get('event', ''), ev.get('time', ''), ev.get('date', ''), ev.get('history_url', '')))


def _extract_name_only(full_label):
    """Extract 'First Last' from 'First Last (TEAM)' format."""
    if not full_label:
        return full_label
    if '(' in full_label:
        return full_label.split('(')[0].strip()
    return full_label.strip()


# ===== Event history =====

def get_event_history(history_url):
    """Return event history rows. The 'meet' field is the actual meet
    name (from meet_pdf_cache.meet_name) when we've parsed that meet's
    PDF; otherwise falls back to swim_type ('Finals'/'Prelims') so the
    UI never shows nothing."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT eh.time, eh.swim_type, eh.date, eh.ct_meet_id,
                   m.meet_name
            FROM event_history eh
            LEFT JOIN meet_pdf_cache m ON m.ct_meet_id = eh.ct_meet_id
            WHERE eh.history_url = ?
            ORDER BY eh.id
        """, (history_url,)).fetchall()
    if not rows:
        return None
    out = []
    for r in rows:
        meet_name = (r['meet_name'] or '').strip() or (r['swim_type'] or '')
        out.append({
            'time': r['time'],
            'meet': meet_name,
            'swim_type': r['swim_type'] or '',
            'date': r['date'] or '',
            'ct_meet_id': r['ct_meet_id'] or '',
        })
    return out


def get_member_meet_history(member_id: int):
    """Return distinct (ct_meet_id, date) tuples across all of one member's
    cached event_history rows. The triangulator uses these to figure out
    which meets to look up in meet_pdf_cache."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT eh.ct_meet_id, eh.date
            FROM event_history eh
            JOIN team_members tm ON tm.ct_id = eh.swimmer_ct_id
            WHERE tm.id = ? AND eh.ct_meet_id IS NOT NULL AND eh.ct_meet_id != ''
        """, (member_id,)).fetchall()
    return [{'ct_meet_id': r['ct_meet_id'], 'date': r['date']} for r in rows]


def save_event_history(history_url, swimmer_ct_id, event_name, history):
    with get_conn() as conn:
        conn.execute("DELETE FROM event_history WHERE history_url = ?", (history_url,))
        for h in history:
            conn.execute("""
                INSERT INTO event_history
                  (swimmer_ct_id, event, history_url, time, swim_type, date, ct_meet_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (swimmer_ct_id, event_name, history_url,
                  h.get('time', ''), h.get('meet', ''), h.get('date', ''),
                  h.get('ct_meet_id', '')))


# ===== Meet PDF cache =====

def get_meet_cache(ct_meet_id: str):
    """Return cached meet metadata, or None if never seen."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ct_meet_id, meet_name, start_date, end_date, pdf_url, "
            "parsed_at, note FROM meet_pdf_cache WHERE ct_meet_id = ?",
            (ct_meet_id,)
        ).fetchone()
    return dict(row) if row else None


def save_meet_cache(ct_meet_id: str, meet_name: str = None,
                    start_date: str = None, end_date: str = None,
                    pdf_url: str = None, parsed_at: str = None,
                    note: str = None):
    """Upsert one row of meet_pdf_cache. Only non-None args overwrite existing."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT meet_name, start_date, end_date, pdf_url, parsed_at, note "
            "FROM meet_pdf_cache WHERE ct_meet_id = ?", (ct_meet_id,)
        ).fetchone()
        if existing:
            conn.execute("""
                UPDATE meet_pdf_cache
                SET meet_name = COALESCE(?, meet_name),
                    start_date = COALESCE(?, start_date),
                    end_date = COALESCE(?, end_date),
                    pdf_url = COALESCE(?, pdf_url),
                    parsed_at = COALESCE(?, parsed_at),
                    note = ?
                WHERE ct_meet_id = ?
            """, (meet_name, start_date, end_date, pdf_url,
                  parsed_at, note, ct_meet_id))
        else:
            conn.execute("""
                INSERT INTO meet_pdf_cache
                  (ct_meet_id, meet_name, start_date, end_date, pdf_url,
                   parsed_at, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (ct_meet_id, meet_name, start_date, end_date, pdf_url,
                  parsed_at, note))


def save_meet_pdf_swimmers(ct_meet_id: str, swimmers: list):
    """Bulk insert parsed PDF rows. Replaces any existing rows for this meet."""
    with get_conn() as conn:
        conn.execute("DELETE FROM meet_pdf_swimmers WHERE ct_meet_id = ?",
                     (ct_meet_id,))
        for sw in swimmers:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO meet_pdf_swimmers
                      (ct_meet_id, name_key, first_name, last_name, age, gender, team)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (ct_meet_id, sw.get('name_key'), sw.get('first'),
                      sw.get('last'), sw.get('age'), sw.get('gender'),
                      sw.get('team')))
            except Exception:
                continue


def reset_pdf_caches():
    """Clear all parsed-PDF data. Use when the underlying parser/data has
    been improved and we want fresh re-parsing on the next age-fill run."""
    with get_conn() as conn:
        conn.execute("DELETE FROM meet_pdf_swimmers")
        conn.execute("DELETE FROM meet_pdf_cache")


def reset_member_triangulation():
    """Clear all triangulation-derived fields on every team_member.
    Manually-entered birth_year/birth_month from the roster modal are
    preserved if they match the priority order in _compute_age (we don't
    distinguish coach-entered from auto-derived, so both get cleared).
    Coach should re-enter manual overrides after resetting if needed."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE team_members
            SET birth_year = NULL, birth_month = NULL,
                age_observed = NULL, age_observed_at = NULL,
                age_synced_at = NULL, age_window_days = NULL
        """)


def lookup_swimmer_age_at_meet(ct_meet_id: str, name_key: str,
                                prefer_team: str = None):
    """Return {age, gender, team} for our swimmer at this meet, or None.

    Lookup priority:
      1. Exact name_key + prefer_team (best signal, no ambiguity)
      2. Exact name_key, any team (handles team-changers)
      3. Prefix name_key + prefer_team (handles middle names — old data
         stored 'wetmore_harpergrace' from a multi-word first capture;
         lookup 'wetmore_harper%' still finds her)
      4. Prefix name_key, any team
    """
    prefix_key = name_key + '%'
    with get_conn() as conn:
        if prefer_team:
            row = conn.execute(
                "SELECT age, gender, team FROM meet_pdf_swimmers "
                "WHERE ct_meet_id = ? AND name_key = ? AND team = ? LIMIT 1",
                (ct_meet_id, name_key, prefer_team)
            ).fetchone()
            if row:
                return dict(row)
        row = conn.execute(
            "SELECT age, gender, team FROM meet_pdf_swimmers "
            "WHERE ct_meet_id = ? AND name_key = ? LIMIT 1",
            (ct_meet_id, name_key)
        ).fetchone()
        if row:
            return dict(row)
        if prefer_team:
            row = conn.execute(
                "SELECT age, gender, team FROM meet_pdf_swimmers "
                "WHERE ct_meet_id = ? AND name_key LIKE ? AND team = ? LIMIT 1",
                (ct_meet_id, prefix_key, prefer_team)
            ).fetchone()
            if row:
                return dict(row)
        row = conn.execute(
            "SELECT age, gender, team FROM meet_pdf_swimmers "
            "WHERE ct_meet_id = ? AND name_key LIKE ? LIMIT 1",
            (ct_meet_id, prefix_key)
        ).fetchone()
    return dict(row) if row else None


# ===== Sync logging =====

def log_sync(ct_id, action, status, message=''):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sync_log (swimmer_ct_id, action, status, message) VALUES (?, ?, ?, ?)",
            (ct_id, action, status, message)
        )


# ===== Admin helpers =====

def get_all_swimmers():
    """For Refresh All / Club page. Returns full list."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT ct_id, name, team_code, last_synced,
                   (SELECT COUNT(*) FROM best_times WHERE swimmer_ct_id = swimmers.ct_id) AS event_count
            FROM swimmers
            ORDER BY name
        """).fetchall()
    return [dict(r) for r in rows]


def get_swimmer_count():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM swimmers").fetchone()[0]


def set_roster(ct_id, roster):
    with get_conn() as conn:
        conn.execute("UPDATE swimmers SET roster = ? WHERE ct_id = ?", (roster, ct_id))


def get_distinct_rosters():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT roster FROM swimmers WHERE roster IS NOT NULL AND roster != '' ORDER BY roster"
        ).fetchall()
    return [r[0] for r in rows]


def get_cached_swimmer_count():
    """Swimmers that have been fully synced (have last_synced)."""
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM swimmers WHERE last_synced IS NOT NULL").fetchone()[0]


# ===== TEAM MEMBERS (managed roster) =====

import json as _json
from datetime import date as _date


def _safe_get(row, key):
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def _compute_age(row):
    """Compute age in priority order:
      1. birth_year + birth_month — full info, exact computation
      2. age_observed + age_observed_at — most recent championship-age
         observation, drift-corrected by elapsed time. Used when birth_month
         is unknown because the July-midpoint fallback gives wrong answers
         for swimmers with birthdays in early-spring or late-fall.
      3. birth_year only — last-resort July midpoint
    Returns int or None.
    """
    today = _date.today()
    by = _safe_get(row, 'birth_year')
    bm = _safe_get(row, 'birth_month')
    obs = _safe_get(row, 'age_observed')
    obs_at = _safe_get(row, 'age_observed_at')

    # Priority 1: full birth info
    if by and bm:
        return max(0, today.year - int(by) - (today.month < int(bm)))

    # Priority 2: recent observed age (preferred over year-only guess
    # because a fresh observation already encodes the championship-age
    # rule from a real meet)
    if obs is not None and obs_at:
        try:
            d = _date.fromisoformat(str(obs_at)[:10])
            years_since = today.year - d.year - ((today.month, today.day) < (d.month, d.day))
            return max(0, int(obs) + max(0, years_since))
        except (ValueError, TypeError):
            pass

    # Priority 3: birth_year only — last-resort July midpoint
    if by:
        return max(0, today.year - int(by) - (today.month < 7))

    return None


def _age_source(row):
    """Return 'entered', 'observed', or None — for UI hints."""
    if _safe_get(row, 'birth_year'):
        return 'entered'
    if _safe_get(row, 'age_observed') is not None:
        return 'observed'
    return None


def _member_row_to_dict(row, include_birth=False, include_parent_email=True):
    """Serialize a team_members row. Strips birth fields by default for privacy."""
    d = dict(row)
    d['age'] = _compute_age(d)
    d['age_source'] = _age_source(d)
    # Always strip the legacy dob field
    d.pop('dob', None)
    if not include_birth:
        d.pop('birth_year', None)
        d.pop('birth_month', None)
        # age_observed_at is just a timestamp, not PII; keep it visible
    if not include_parent_email:
        d.pop('parent_email', None)
    return d


def list_team_members(include_birth=False, parent_email=None):
    """Return all team_members. If parent_email given, restricts to swimmers
    linked to that email."""
    sql = """
        SELECT tm.id, tm.first_name, tm.last_name, tm.roster,
               tm.gender, tm.birth_year, tm.birth_month,
               tm.age_observed, tm.age_observed_at,
               tm.age_synced_at, tm.age_window_days,
               tm.parent_email,
               tm.ct_id, tm.notes, tm.created_at, tm.updated_at,
               s.name AS ct_name, s.team_code AS ct_team, s.last_synced
        FROM team_members tm
        LEFT JOIN swimmers s ON s.ct_id = tm.ct_id
    """
    args = ()
    if parent_email:
        sql += " WHERE LOWER(tm.parent_email) = ? "
        args = (parent_email.strip().lower(),)
    sql += " ORDER BY tm.roster, tm.last_name, tm.first_name "
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_member_row_to_dict(r, include_birth=include_birth) for r in rows]


def parent_email_exists(email):
    """True if any team_member has this email registered."""
    if not email:
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM team_members WHERE LOWER(parent_email) = ? LIMIT 1",
            (email.strip().lower(),)
        ).fetchone()
    return row is not None


def list_team_members_with_times(include_birth=False, parent_email=None):
    """For the Coach Dashboard: each member with their cached best_times list."""
    members = list_team_members(include_birth=include_birth, parent_email=parent_email)
    if not members:
        return []
    ct_ids = [m['ct_id'] for m in members if m.get('ct_id')]
    times_by_ct = {}
    if ct_ids:
        placeholders = ','.join('?' * len(ct_ids))
        with get_conn() as conn:
            rows = conn.execute(
                f"SELECT swimmer_ct_id, event, time, date, history_url "
                f"FROM best_times WHERE swimmer_ct_id IN ({placeholders})",
                ct_ids
            ).fetchall()
        for r in rows:
            times_by_ct.setdefault(r['swimmer_ct_id'], []).append({
                'event': r['event'], 'time': r['time'],
                'date': r['date'], 'history_url': r['history_url'],
            })
    for m in members:
        m['best_times'] = times_by_ct.get(m.get('ct_id'), [])
    return members


def get_team_member(member_id, include_birth=False):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM team_members WHERE id = ?", (member_id,)).fetchone()
    if not row:
        return None
    return _member_row_to_dict(row, include_birth=include_birth)


def _coerce_year(v):
    try:
        n = int(v)
        if 1990 <= n <= _date.today().year:
            return n
    except (TypeError, ValueError):
        pass
    return None


def _coerce_month(v):
    try:
        n = int(v)
        if 1 <= n <= 12:
            return n
    except (TypeError, ValueError):
        pass
    return None


def _coerce_email(v):
    if v is None:
        return None
    s = str(v).strip().lower()
    if not s or '@' not in s:
        return None
    return s


def create_team_member(first_name, last_name, roster=None, gender=None,
                       birth_year=None, birth_month=None, notes=None,
                       parent_email=None):
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO team_members
                (first_name, last_name, roster, gender,
                 birth_year, birth_month, parent_email, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (first_name.strip(), last_name.strip(),
              roster.strip() if roster else None,
              gender.strip().upper()[:1] if gender else None,
              _coerce_year(birth_year),
              _coerce_month(birth_month),
              _coerce_email(parent_email),
              notes,
              _utcnow_iso()))
        return cur.lastrowid


def update_team_member(member_id, **fields):
    """Update only provided fields. Empty/None values are NOT applied so the
    coach can leave a field blank in the edit form to keep existing value."""
    if not fields:
        return False
    sets = []
    args = []
    for col in ('first_name', 'last_name', 'roster', 'gender',
                'birth_year', 'birth_month', 'parent_email', 'ct_id', 'notes'):
        if col not in fields:
            continue
        v = fields[col]
        # 'notes' is allowed to be cleared (empty string saves NULL/empty),
        # but other fields keep prior value when blank.
        if col != 'notes' and v in (None, ''):
            continue
        if col in ('first_name', 'last_name', 'roster'):
            v = v.strip()
        elif col == 'gender':
            v = v.strip().upper()[:1] if v else None
        elif col == 'birth_year':
            v = _coerce_year(v)
            if v is None:
                continue
        elif col == 'birth_month':
            v = _coerce_month(v)
            if v is None:
                continue
        elif col == 'parent_email':
            v = _coerce_email(v)
            if v is None:
                continue
        elif col == 'notes':
            # keep raw text; just trim trailing whitespace
            v = (v or '').rstrip()
        sets.append(f"{col} = ?")
        args.append(v)
    if not sets:
        return False
    sets.append("updated_at = ?")
    args.append(_utcnow_iso())
    args.append(member_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE team_members SET {', '.join(sets)} WHERE id = ?", args)
    return True


def save_member_triangulation(member_id, birth_year=None, birth_month=None,
                              window_days=None, age_observed=None, observed_at=None):
    """Save the outcome of a swimstandards triangulation pass.
    Records age_synced_at = today regardless of whether year/month were narrowed."""
    today = _date.today().isoformat()
    sets = ['age_synced_at = ?']
    args = [today]
    if window_days is not None:
        sets.append('age_window_days = ?')
        args.append(int(window_days))
    if birth_year is not None:
        y = _coerce_year(birth_year)
        if y:
            sets.append('birth_year = ?')
            args.append(y)
    if birth_month is not None:
        mo = _coerce_month(birth_month)
        if mo:
            sets.append('birth_month = ?')
            args.append(mo)
    if age_observed is not None:
        try:
            n = int(age_observed)
            if 5 <= n <= 25:
                sets.append('age_observed = ?')
                args.append(n)
                sets.append('age_observed_at = ?')
                args.append(observed_at or today)
        except (TypeError, ValueError):
            pass
    sets.append('updated_at = ?')
    args.append(_utcnow_iso())
    args.append(member_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE team_members SET {', '.join(sets)} WHERE id = ?", args)
        # Sanity check: if birth_year + age_observed disagree by more than 1 year,
        # clear birth_year/birth_month. The triangulation may not have been able
        # to pin down a year (e.g. window crossed a year boundary, or contradictory
        # records). Whatever was previously stored is likely stale or wrong, so
        # we'd rather show 'no year' (and fall back to age_observed) than show
        # a confidently-wrong age.
        row = conn.execute("""
            SELECT birth_year, birth_month, age_observed, age_observed_at
            FROM team_members WHERE id = ?
        """, (member_id,)).fetchone()
        if row and row['birth_year'] and row['age_observed'] is not None and row['age_observed_at']:
            today_d = _date.today()
            implied = today_d.year - int(row['birth_year']) - (
                today_d.month < int(row['birth_month'] or 7)
            )
            try:
                obs_at = _date.fromisoformat(str(row['age_observed_at'])[:10])
                drift = today_d.year - obs_at.year - ((today_d.month, today_d.day) < (obs_at.month, obs_at.day))
                obs_today = int(row['age_observed']) + max(0, drift)
            except (ValueError, TypeError):
                obs_today = int(row['age_observed'])
            if abs(implied - obs_today) > 1:
                conn.execute("""
                    UPDATE team_members
                       SET birth_year = NULL, birth_month = NULL, updated_at = ?
                     WHERE id = ?
                """, (_utcnow_iso(), member_id))
        # Pass 2: random-seed pattern detection.
        # Earlier test data was seeded with birth_month=6 (year midpoint), random
        # gender, and random birth_year. For swimmers that swimstandards can't
        # find (404 → age_observed never set) this seed data is stuck and the
        # earlier sanity check above never fires. Detect the seed signature and
        # clear birth_year, birth_month, gender so the coach can re-enter, OR
        # so a future swimstandards sync (once they're indexed) can populate
        # cleanly. Conservative trigger: must have just synced (age_synced_at
        # is today) AND have the exact signature.
        row2 = conn.execute("""
            SELECT birth_year, birth_month, age_observed, age_synced_at
            FROM team_members WHERE id = ?
        """, (member_id,)).fetchone()
        if (row2
                and row2['age_synced_at'] == today
                and row2['birth_month'] == 6
                and row2['age_observed'] is None
                and row2['birth_year'] is not None):
            conn.execute("""
                UPDATE team_members
                   SET birth_year = NULL, birth_month = NULL, gender = NULL,
                       updated_at = ?
                 WHERE id = ?
            """, (_utcnow_iso(), member_id))
    return True


def update_member_observed_age(ct_id, age, observed_at=None):
    """Push a CT-Swim-scraped age into all team_members linked to this ct_id.
    Used by the swimmer fetch flow. No-op if ct_id has no team_member."""
    if ct_id is None or age is None:
        return 0
    try:
        age = int(age)
    except (TypeError, ValueError):
        return 0
    if not (5 <= age <= 25):
        return 0
    obs_at = observed_at or _date.today().isoformat()
    with get_conn() as conn:
        cur = conn.execute("""
            UPDATE team_members
               SET age_observed = ?, age_observed_at = ?, updated_at = ?
             WHERE ct_id = ?
        """, (age, obs_at, _utcnow_iso(), ct_id))
        return cur.rowcount


def delete_team_member(member_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM team_members WHERE id = ?", (member_id,))


def link_team_member_to_swimmer(member_id, ct_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE team_members SET ct_id = ?, updated_at = ? WHERE id = ?",
            (ct_id, _utcnow_iso(), member_id)
        )


CLUB_TEAM_CODE = os.environ.get('CLUB_TEAM_CODE', 'IVY')


def auto_link_team_members():
    """Walk team_members and link/re-link to cached swimmers by name.

    Two passes:
    1. Link unlinked members (ct_id IS NULL) to a matching swimmer.
    2. RE-EVALUATE already-linked members whose current swimmer's
       team_code is NOT CLUB_TEAM_CODE — if a club-team alternative
       with the same name now exists in the swimmers cache, switch.
       This corrects mis-links from earlier scrapes (e.g. Emma Baker
       was linked to SMST before IVY's Emma Baker got cached).

    Both passes prefer CLUB_TEAM_CODE swimmers when multiple namesakes
    exist, falling back to first match (preserves linkage for swimmers
    who legitimately changed teams like Simon Allegra FVYT→IVY).
    """
    import re
    def norm(s):
        return re.sub(r'[^a-z0-9]', '', (s or '').lower())

    with get_conn() as conn:
        all_swimmers = conn.execute("SELECT ct_id, name, team_code FROM swimmers").fetchall()
        by_name = {}
        for s in all_swimmers:
            key = norm(s['name'])
            by_name.setdefault(key, []).append(dict(s))

        def _pick(matches):
            club = [m for m in matches if (m.get('team_code') or '').upper() == CLUB_TEAM_CODE]
            return club[0] if club else matches[0]

        # Pass 1: previously unlinked members
        unlinked = conn.execute(
            "SELECT id, first_name, last_name, roster FROM team_members WHERE ct_id IS NULL"
        ).fetchall()
        linked = []
        conflicts = []
        unmatched = []
        for tm in unlinked:
            full = f"{tm['first_name']} {tm['last_name']}"
            matches = by_name.get(norm(full), [])
            if not matches:
                unmatched.append(full)
                continue
            chosen = _pick(matches)
            if len(matches) > 1:
                conflicts.append({
                    'member': full,
                    'options': [m['team_code'] for m in matches],
                    'chose': chosen['team_code'],
                })
            conn.execute(
                "UPDATE team_members SET ct_id = ?, updated_at = ? WHERE id = ?",
                (chosen['ct_id'], _utcnow_iso(), tm['id'])
            )
            linked.append({'member': full, 'ct_team': chosen['team_code']})

        # Pass 2: members linked to a non-club-team swimmer — re-evaluate
        already_linked = conn.execute("""
            SELECT tm.id, tm.first_name, tm.last_name, tm.ct_id,
                   s.team_code AS current_team
            FROM team_members tm
            JOIN swimmers s ON s.ct_id = tm.ct_id
            WHERE UPPER(COALESCE(s.team_code, '')) != ?
        """, (CLUB_TEAM_CODE,)).fetchall()
        relinked = []
        for tm in already_linked:
            full = f"{tm['first_name']} {tm['last_name']}"
            matches = by_name.get(norm(full), [])
            club = [m for m in matches if (m.get('team_code') or '').upper() == CLUB_TEAM_CODE]
            if not club:
                continue  # no club option exists — leave the historical link alone
            new = club[0]
            conn.execute(
                "UPDATE team_members SET ct_id = ?, updated_at = ? WHERE id = ?",
                (new['ct_id'], _utcnow_iso(), tm['id'])
            )
            relinked.append({
                'member': full,
                'from_team': tm['current_team'],
                'to_team': new['team_code'],
            })

    return {
        'linked': len(linked),
        'relinked': len(relinked),
        'unmatched': unmatched,
        'conflicts': conflicts,
        'linked_sample': linked[:5],
        'relinked_sample': relinked[:5],
    }


def count_team_members():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM team_members").fetchone()[0]


def seed_team_members_from_json(json_path):
    """Import team members from a JSON file ONLY if the table is currently empty.
    Accepts either { dob: 'YYYY-MM-DD' } (legacy) or { birth_year, birth_month }.
    """
    if count_team_members() > 0:
        return 0
    if not os.path.exists(json_path):
        return 0
    try:
        with open(json_path) as f:
            data = _json.load(f)
    except Exception:
        return 0
    members = data.get('team_members', [])
    n = 0
    for m in members:
        first = (m.get('first_name') or '').strip()
        last = (m.get('last_name') or '').strip()
        if not first or not last:
            continue
        by = m.get('birth_year')
        bm = m.get('birth_month')
        # Legacy: split a YYYY-MM-DD dob into year/month and discard the day
        if not by and m.get('dob'):
            parts = str(m['dob']).split('-')
            if len(parts) >= 2:
                by = parts[0]
                bm = parts[1]
        create_team_member(
            first_name=first,
            last_name=last,
            roster=m.get('roster') or None,
            gender=m.get('gender') or None,
            birth_year=by,
            birth_month=bm,
        )
        n += 1
    return n

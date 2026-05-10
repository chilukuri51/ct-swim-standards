import os
import re
import json
from datetime import timedelta
from functools import wraps
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, jsonify, request, session, redirect, url_for

import db
import batch
import age_filler
import standards_store

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-me-in-production-please-a8f3d9x2')
# Make admin/coach sessions stick for 30 days so they don't get a sudden
# 'authentication required' mid-task. Combined with session.permanent=True
# in the login handler, this is the actual cookie lifetime.
app.permanent_session_lifetime = timedelta(days=30)
# Cap PDF uploads at 10 MB. Real CT Swim meet PDFs are typically
# 100-500 KB; anything larger is almost certainly the wrong file.
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

BASE_URL = "https://fast.ctswim.org/CTNet"

# ===== Three role-based accounts =====
# Each role has its own username/password (override via env vars in production)
USERS = {
    'admin': os.environ.get('ADMIN_PASSWORD', 'ItsNaga!23'),
    'coach': os.environ.get('COACH_PASSWORD', 'ItsEllis!23'),
}
# A single shared parent password protects parent-flavored access. The
# parent's identity (which kids they see) comes from their email, which
# must already exist as `parent_email` on at least one team_member row.
PARENT_PASSWORD = os.environ.get('PARENT_PASSWORD', 'IvyParent!23')

# Feature access matrix
ROLE_PERMISSIONS = {
    'admin': {'search', 'refresh', 'batch', 'club', 'roster', 'roster_edit', 'standards', 'standards_edit', 'dashboard', 'whatif'},
    'coach': {'search', 'club', 'roster', 'roster_edit', 'standards', 'standards_edit', 'dashboard', 'whatif'},
    # Parent sees only their kids' profile; nothing club-wide, no editing.
    'parent': {'roster'},
}


# Initialize DB on import
db.init_db()

# Seed team_members from team_roster.json on first startup (no-op if table populated).
# Looks in the persistent data dir first (so the file survives deploys), then falls
# back to the project-bundled copy for local dev.
import paths
_TEAM_ROSTER_CANDIDATES = [
    os.path.join(paths.DATA_DIR, 'team_roster.json'),
    os.path.join(paths.PROJECT_DATA_DIR, 'team_roster.json'),
]
TEAM_ROSTER_JSON = next((p for p in _TEAM_ROSTER_CANDIDATES if os.path.exists(p)),
                       _TEAM_ROSTER_CANDIDATES[0])
_seeded = db.seed_team_members_from_json(TEAM_ROSTER_JSON)
if _seeded:
    app.logger.info(f"Seeded {_seeded} team members from {TEAM_ROSTER_JSON}")
app.logger.info(
    f"Persist root: {paths.PERSIST_ROOT or '(local dev)'} | "
    f"DB: {paths.DB_PATH} | DATA_DIR: {paths.DATA_DIR}"
)

# Ensure standards.json (working copy) exists, copying from default if needed
standards_store.ensure_seeded()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('logged_in'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('login', next=request.path))
        return view(*args, **kwargs)
    return wrapped


def role_required(*allowed_roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            user_role = session.get('role', 'coach')
            if user_role not in allowed_roles:
                if request.path.startswith('/api/'):
                    return jsonify({'error': 'Forbidden'}), 403
                return redirect(url_for('index'))
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ===== CT Swim scraping helpers =====

def get_session_and_tokens():
    sess = requests.Session()
    r = sess.get(f"{BASE_URL}/DatabaseQuery.aspx?Opt=BT", timeout=10)
    return sess, _extract_tokens(r.text)


def _extract_tokens(html):
    vs = re.search(r'name="__VIEWSTATE"[^>]*value="([^"]*)"', html)
    vsg = re.search(r'name="__VIEWSTATEGENERATOR"[^>]*value="([^"]*)"', html)
    ev = re.search(r'name="__EVENTVALIDATION"[^>]*value="([^"]*)"', html)
    return {
        '__VIEWSTATE': vs.group(1) if vs else '',
        '__VIEWSTATEGENERATOR': vsg.group(1) if vsg else '',
        '__EVENTVALIDATION': ev.group(1) if ev else '',
    }


def ct_search_swimmers(last_name):
    """Search CT Swim for swimmers by last name. Returns (swimmers, tokens, cookies)."""
    sess, tokens = get_session_and_tokens()
    data = {
        **tokens,
        'ddl_SearchType': 'Last Name Starts With',
        'tb_SearchFor': last_name,
        '_ctl0': 'Search'
    }
    r = sess.post(f"{BASE_URL}/DatabaseQuery.aspx?Opt=BT", data=data, timeout=15)
    html = r.text

    swimmers = []
    for match in re.finditer(
        r'name="SwimmerSelect" value="([^"]*)"[^>]*/>'
        r'<label[^>]*>([^<]*)</label>',
        html
    ):
        swimmer_id = match.group(1)
        label = match.group(2).strip()
        name_match = re.match(r'(.+?)\s*\((\w+)\)', label)
        if name_match:
            swimmers.append({
                'id': swimmer_id,
                'name': name_match.group(1).strip(),
                'team': name_match.group(2).strip()
            })

    return swimmers, _extract_tokens(html), sess.cookies.get_dict()


def _extract_age_from_ct_html(html, swimmer_name=''):
    """Best-effort age scrape from a CT Swim Best Times page.
    Tries multiple patterns; returns int or None. Safe to call on any HTML.
    """
    # Search both the swimmer label and the broader page text.
    haystacks = []
    if swimmer_name:
        haystacks.append(swimmer_name)
    if html:
        # Strip tags so '12 (F)' style markers aren't broken across HTML
        text = re.sub(r'<[^>]+>', ' ', html)
        haystacks.append(text)
    patterns = [
        r'\bAge\s*[:#-]?\s*(\d{1,2})\b',
        r'\b(\d{1,2})\s*\(\s*[MF]\s*\)',
        r'\b(\d{1,2})\s*Y(?:ears?|rs?)?\b',
    ]
    for hay in haystacks:
        for p in patterns:
            m = re.search(p, hay, re.IGNORECASE)
            if m:
                try:
                    n = int(m.group(1))
                    if 5 <= n <= 25:
                        return n
                except ValueError:
                    continue
    return None


def ct_fetch_best_times(swimmer_id, tokens, cookies):
    """Fetch best times for a swimmer. Returns {swimmer_name, events, age}."""
    sess = requests.Session()
    for k, v in cookies.items():
        sess.cookies.set(k, v)
    data = {
        **tokens,
        'SwimmerSelect': swimmer_id,
        'GoB': 'Display Times'
    }
    r = sess.post(f"{BASE_URL}/DatabaseQuery.aspx?Opt=BT", data=data, timeout=15)
    soup = BeautifulSoup(r.text, 'html.parser')

    swimmer_name = ''
    label2 = soup.find('span', id='Label2')
    if label2:
        swimmer_name = label2.get_text(strip=True)

    age = _extract_age_from_ct_html(r.text, swimmer_name)

    events = []
    table = soup.find('table', id='Table1')
    if table:
        for row in table.find_all('tr')[1:]:
            cells = row.find_all('td')
            if len(cells) >= 4:
                event_code = cells[0].get_text(strip=True)
                stroke = cells[1].get_text(strip=True)
                time = cells[2].get_text(strip=True)
                date = cells[3].get_text(strip=True)
                event_link = ''
                if len(cells) >= 5:
                    link_tag = cells[4].find('a', string=re.compile(r'\[Event\]'))
                    if link_tag:
                        event_link = link_tag.get('href', '')
                events.append({
                    'event': f"{event_code} {stroke}",
                    'time': time,
                    'date': date,
                    'history_url': event_link
                })

    return {'swimmer_name': swimmer_name, 'events': events, 'age': age}


def ct_fetch_event_history(history_url):
    """Fetch event history. Returns {title, history}.

    Each row's 4th cell holds a [Meet] link to SwimmerAtMeet.aspx?...&m=<id>.
    We capture that meet_id so age_filler can look up the swimmer's age in
    the corresponding result PDF (the swim_type column only says
    'Finals'/'Prelims', not the actual meet name).
    """
    full_url = f"{BASE_URL}/{history_url}"
    r = requests.get(full_url, timeout=15)
    soup = BeautifulSoup(r.text, 'html.parser')

    title = ''
    label2 = soup.find('span', id='Label2')
    label3 = soup.find('span', id='Label3')
    if label2:
        title = label2.get_text(strip=True)
    if label3:
        title = label3.get_text(strip=True) + ' - ' + title

    history = []
    # The history table is the one whose header is Time | Swim | Date | [Meet].
    target_table = None
    for tbl in soup.find_all('table'):
        rows = tbl.find_all('tr')
        if not rows:
            continue
        hdr = [c.get_text(strip=True) for c in rows[0].find_all(['td', 'th'])]
        if hdr[:3] == ['Time', 'Swim', 'Date']:
            target_table = tbl
            break
    if target_table:
        for row in target_table.find_all('tr')[1:]:
            cells = row.find_all('td')
            if len(cells) < 3:
                continue
            time_val = cells[0].get_text(strip=True)
            swim_type = cells[1].get_text(strip=True)
            date_val = cells[2].get_text(strip=True)
            meet_id = ''
            if len(cells) >= 4:
                link = cells[3].find('a', href=True)
                if link:
                    m = re.search(r'm=(\d+)', link['href'])
                    if m:
                        meet_id = m.group(1)
            if time_val and time_val != 'Time':
                history.append({
                    'time': time_val,
                    'meet': swim_type,
                    'date': date_val,
                    'ct_meet_id': meet_id,
                })

    return {'title': title, 'history': history}


def ct_fetch_and_cache_swimmer(ct_id):
    """One-shot: search CT Swim for this swimmer's ID, fetch best times, save to cache.
    Used by per-swimmer refresh and batch refresh.
    """
    # To fetch a specific swimmer, we need to do a search first (ASP.NET requires the selection flow)
    # Pull existing name from cache to do a search, then pick this specific swimmer by ct_id.
    cached = db.get_best_times(ct_id)
    # Fall back: try getting swimmer row directly
    with db.get_conn() as conn:
        row = conn.execute("SELECT name FROM swimmers WHERE ct_id = ?", (ct_id,)).fetchone()
    if not row:
        raise ValueError(f"Swimmer {ct_id} not in cache; search for them first")
    last_name = row['name'].split()[-1]

    swimmers, tokens, cookies = ct_search_swimmers(last_name)
    if not any(s['id'] == ct_id for s in swimmers):
        # Swimmer no longer findable with that last name
        db.log_sync(ct_id, 'refresh', 'error', f'Not found in CT Swim search for "{last_name}"')
        raise ValueError('Swimmer not found in CT Swim search')

    result = ct_fetch_best_times(ct_id, tokens, cookies)
    db.save_best_times(ct_id, result['swimmer_name'], result['events'])
    if result.get('age'):
        db.update_member_observed_age(ct_id, result['age'])
    db.log_sync(ct_id, 'refresh', 'ok', f'Fetched {len(result["events"])} events')
    return result


# ===== Auth routes =====

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        # Coach / admin: fixed username + per-role password.
        if username in USERS and password == USERS[username]:
            session['logged_in'] = True
            session['username'] = username
            session['role'] = username
            session.permanent = True
            return redirect(request.args.get('next') or url_for('index'))
        # Parent: looks like an email + shared parent password + at least
        # one team_member must already have parent_email = this email.
        if '@' in username and password == PARENT_PASSWORD:
            with db.get_conn() as conn:
                hit = conn.execute(
                    "SELECT 1 FROM team_members WHERE LOWER(parent_email) = ? LIMIT 1",
                    (username,)
                ).fetchone()
            if hit:
                session['logged_in'] = True
                session['username'] = username
                session['role'] = 'parent'
                session['parent_email'] = username
                session.permanent = True
                return redirect(request.args.get('next') or url_for('index'))
            error = 'No swimmers linked to that email yet — ask the coach to add it on your roster entry.'
        else:
            error = error or 'Invalid username or password'
    if session.get('logged_in'):
        return redirect(url_for('index'))
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ===== Main app =====

def _load_world_aquatics_points():
    """Read the committed 2026 World Aquatics base-times JSON. Returns a
    minimal dict with just the data the client needs (the metadata fields
    are useful for documentation; not shipped)."""
    import json as _json
    p = os.path.join(paths.PROJECT_DATA_DIR, 'world_aquatics_points.json')
    try:
        with open(p) as f:
            blob = _json.load(f)
        return {'base_times_lcm': blob.get('base_times_lcm', {})}
    except (OSError, _json.JSONDecodeError):
        return {'base_times_lcm': {}}


def _asset_version() -> str:
    """Cache-buster: max mtime of static JS files, hex-encoded."""
    try:
        import os
        paths = [
            os.path.join(app.root_path, 'static', 'app.js'),
            os.path.join(app.root_path, 'static', 'standards_data.js'),
        ]
        return format(int(max(os.path.getmtime(p) for p in paths if os.path.exists(p))), 'x')
    except Exception:
        return '0'


@app.route('/')
@login_required
def index():
    role = session.get('role', 'coach')
    perms = list(ROLE_PERMISSIONS.get(role, set()))
    return render_template('index.html',
                           role=role,
                           username=session.get('username', ''),
                           permissions=perms,
                           standards=standards_store.load(),
                           wa_points=_load_world_aquatics_points(),
                           asset_version=_asset_version())


# ===== API: Search swimmers =====
# Behavior: check cache first, only hit CT Swim if cache has <5 results.

@app.route('/api/search', methods=['POST'])
@login_required
def search_swimmer():
    last_name = request.json.get('last_name', '').strip()
    if not last_name:
        return jsonify({'error': 'Last name is required'}), 400

    # Always check cache first
    cached_swimmers = db.search_swimmers_by_last_name(last_name)

    # If cache has results, use them (fast, no CT Swim hit)
    if cached_swimmers:
        return jsonify({
            'swimmers': cached_swimmers,
            'cached': True,
            'source': 'cache',
        })

    # Otherwise fall back to CT Swim
    try:
        swimmers, tokens, cookies = ct_search_swimmers(last_name)
        # Cache the swimmer list for next time
        db.upsert_swimmers_bulk(swimmers)
        return jsonify({
            'swimmers': swimmers,
            'tokens': tokens,
            'cookies': cookies,
            'cached': False,
            'source': 'ctswim',
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===== API: Best times =====
# Behavior: always read from cache. If not cached, fetch once and save.

@app.route('/api/best_times', methods=['POST'])
@login_required
def get_best_times():
    swimmer_id = request.json.get('swimmer_id', '')
    tokens = request.json.get('tokens', {})
    cookies = request.json.get('cookies', {})

    if not swimmer_id:
        return jsonify({'error': 'Missing swimmer_id'}), 400

    # Try cache first
    cached = db.get_best_times(swimmer_id)
    if cached and cached.get('events'):
        return jsonify(cached)

    # Not in cache - need to fetch. This only happens on first view.
    if not tokens:
        # We got a swimmer_id from cache (no search was done), so we don't have tokens.
        # Do a fresh CT Swim fetch using the cached swimmer's name.
        try:
            result = ct_fetch_and_cache_swimmer(swimmer_id)
            result['cached'] = False
            return jsonify(result)
        except Exception as e:
            return jsonify({'error': f'Cache miss and refresh failed: {str(e)}'}), 500

    try:
        result = ct_fetch_best_times(swimmer_id, tokens, cookies)
        db.save_best_times(swimmer_id, result['swimmer_name'], result['events'])
        if result.get('age'):
            db.update_member_observed_age(swimmer_id, result['age'])
        result['cached'] = False
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===== API: Event history =====

@app.route('/api/event_history', methods=['POST'])
@login_required
def get_event_history():
    history_url = request.json.get('history_url', '')
    swimmer_id = request.json.get('swimmer_id', '')
    event_name = request.json.get('event_name', '')

    if not history_url:
        return jsonify({'error': 'Missing history URL'}), 400

    # Check cache first
    cached = db.get_event_history(history_url)
    if cached:
        # Build title from swimmer + event
        with db.get_conn() as conn:
            row = conn.execute("SELECT name, team_code FROM swimmers WHERE ct_id = ?", (swimmer_id,)).fetchone()
        title = ''
        if row:
            name_team = f"{row['name']} ({row['team_code']})" if row['team_code'] else row['name']
            title = f"Swimmer Event History - {name_team}"
        return jsonify({'title': title, 'history': cached, 'cached': True})

    # Not cached - fetch
    try:
        result = ct_fetch_event_history(history_url)
        if swimmer_id:
            db.save_event_history(history_url, swimmer_id, event_name, result['history'])
        result['cached'] = False
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===== API: Refresh a single swimmer (admin only) =====

@app.route('/api/refresh_swimmer', methods=['POST'])
@role_required('admin')
def refresh_swimmer():
    swimmer_id = request.json.get('swimmer_id', '')
    if not swimmer_id:
        return jsonify({'error': 'Missing swimmer_id'}), 400
    try:
        result = ct_fetch_and_cache_swimmer(swimmer_id)
        # Best-effort: catch any newly-linked swimmers in the next age-fill pass
        age_filler.start_autofill(force_all=False)
        return jsonify({'ok': True, 'events_count': len(result.get('events', []))})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===== API: Cache stats (for admin tab) =====

@app.route('/api/cache_stats', methods=['GET'])
@login_required
def cache_stats():
    return jsonify({
        'total_swimmers': db.get_swimmer_count(),
        'cached_swimmers': db.get_cached_swimmer_count(),
    })


# ===== API: Admin batch fetch =====

@app.route('/api/team_members', methods=['GET'])
@role_required('admin', 'coach')
def api_list_team_members():
    members = db.list_team_members()
    return jsonify({'members': members, 'count': len(members)})


@app.route('/api/my_swimmers', methods=['GET'])
@login_required
def api_my_swimmers():
    """Roster + cached best_times for the logged-in role.
    - admin / coach: full team
    - parent: only swimmers whose parent_email matches the session's email

    include_birth=True so the profile modal can compute swim-time age
    client-side (championship-age rule applied per meet date)."""
    role = session.get('role', 'coach')
    parent_email = session.get('parent_email') if role == 'parent' else None
    members = db.list_team_members_with_times(
        include_birth=True, parent_email=parent_email
    )
    return jsonify({'members': members, 'count': len(members)})


@app.route('/api/swimmer_all_swims', methods=['GET'])
@login_required
def api_swimmer_all_swims():
    """All cached swims for one swimmer, flattened across events.
    Powers the profile-modal 'View by Meet' filter."""
    ct_id = (request.args.get('ct_id') or '').strip()
    if not ct_id:
        return jsonify({'error': 'ct_id required'}), 400
    return jsonify({'swims': db.get_member_all_swims(ct_id)})


@app.route('/api/peer_percentile', methods=['GET'])
@login_required
def api_peer_percentile():
    """Anonymized within-roster percentile per event for one swimmer.

    Compares this swimmer's best time against every other roster member
    of the SAME championship age group + gender + course/event. Returns
    just stats — no peer names — so it's safe to expose to a parent.
    """
    ct_id = (request.args.get('ct_id') or '').strip()
    if not ct_id:
        return jsonify({'error': 'ct_id required'}), 400
    members = db.list_team_members_with_times(include_birth=True)
    me = next((m for m in members if m.get('ct_id') == ct_id), None)
    if not me or not me.get('age') or not me.get('gender'):
        return jsonify({'percentiles': {}})

    def age_group(age):
        if age is None: return None
        if age <= 10: return '10/Under'
        if age <= 12: return '11/12'
        if age <= 14: return '13/14'
        if age <= 16: return '15/16'
        return '17/18'

    def time_to_secs(s):
        if not s: return None
        s = s.replace('*', '').strip()
        try:
            parts = s.split(':')
            if len(parts) == 1: return float(parts[0])
            if len(parts) == 2: return float(parts[0]) * 60 + float(parts[1])
            if len(parts) == 3: return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        except (ValueError, IndexError):
            return None

    my_age_grp = age_group(me['age'])
    my_gender = me['gender']

    # Collect peer best times per event (members in same age group + gender)
    peers_by_event = {}  # event -> [seconds]
    for peer in members:
        if peer.get('ct_id') == ct_id:
            continue
        if peer.get('gender') != my_gender:
            continue
        if age_group(peer.get('age')) != my_age_grp:
            continue
        for bt in (peer.get('best_times') or []):
            secs = time_to_secs(bt.get('time'))
            if secs is not None:
                peers_by_event.setdefault(bt['event'], []).append(secs)

    out = {}
    for bt in (me.get('best_times') or []):
        my_secs = time_to_secs(bt.get('time'))
        if my_secs is None:
            continue
        peers = peers_by_event.get(bt['event'], [])
        n = len(peers)
        if n == 0:
            continue
        # Percentile: % of peers we are FASTER than (lower time = better)
        slower = sum(1 for p in peers if p > my_secs)
        # Include self in denominator for cleaner labeling
        pct = round((slower / (n + 1)) * 100)
        out[bt['event']] = {
            'percentile': pct,
            'n_peers': n,
            'age_group': my_age_grp,
        }
    return jsonify({'percentiles': out, 'age_group': my_age_grp, 'gender': my_gender})


@app.route('/api/upcoming_meets', methods=['GET'])
@login_required
def api_upcoming_meets():
    """Return the next ~30 meets from the committed Results.aspx index
    that start on or after today, sorted soonest-first. Used by the
    profile-modal 'Upcoming meets' block."""
    import json as _json
    from datetime import date as _date
    candidates = [
        os.path.join(paths.DATA_DIR, 'meet_index.json'),
        os.path.join(paths.PROJECT_DATA_DIR, 'meet_index.json'),
    ]
    rows = []
    for p in candidates:
        if not os.path.exists(p):
            continue
        try:
            with open(p) as f:
                blob = _json.load(f)
            rows = blob.get('rows', [])
            break
        except (OSError, _json.JSONDecodeError):
            continue
    today_iso = _date.today().isoformat()
    upcoming = []
    seen_meets = set()
    for r in rows:
        sd = r.get('start_date') or ''
        if not sd or sd < today_iso:
            continue
        # Dedup multi-PDF meets — keep one row per (start_date, meet_name).
        key = (sd, (r.get('meet_name') or '').strip())
        if key in seen_meets:
            continue
        seen_meets.add(key)
        upcoming.append({
            'start_date': sd,
            'end_date': r.get('end_date') or sd,
            'meet_name': r.get('meet_name') or '',
            'label': r.get('label') or '',
        })
    upcoming.sort(key=lambda x: x['start_date'])
    return jsonify({'meets': upcoming[:30]})


@app.route('/api/team_members/auto_link', methods=['POST'])
@role_required('admin')
def api_auto_link():
    """Match cached swimmers to team_members by name. Local-only, no CT Swim calls."""
    result = db.auto_link_team_members()
    return jsonify({'ok': True, **result})


@app.route('/api/team_members/autofill_ages/start', methods=['POST'])
@role_required('admin')
def api_autofill_ages_start():
    """Kick off the CT Swim PDF age triangulation job."""
    force = bool((request.json or {}).get('force_all'))
    started = age_filler.start_autofill(force_all=force)
    if not started:
        return jsonify({'error': 'A job is already running. Cancel it first.'}), 409
    return jsonify({'ok': True, 'status': age_filler.get_status()})


@app.route('/api/admin/reset_age_data', methods=['POST'])
@role_required('admin')
def api_reset_age_data():
    """Nuclear option: clear ALL parsed-PDF data and triangulation fields,
    then re-run auto-fill from scratch. Use when stale data from earlier
    buggy parsers is stuck on team_members and Force-all alone won't
    re-derive (because cached parsed_at rows look 'done').

    Body (optional): {"keep_uploaded": true} to preserve manually-uploaded
    PDFs (rows in meet_pdf_cache that have a non-empty pdf_url AND were
    not from auto-discovery). Default false = wipe everything.
    """
    body = request.json or {}
    keep_uploaded = bool(body.get('keep_uploaded'))
    skip_autofill = bool(body.get('skip_autofill'))
    if keep_uploaded:
        # Keep cache rows that have a pdf_url (manually registered) and
        # their parsed swimmer rows. Drop the rest.
        with db.get_conn() as conn:
            conn.execute("""
                DELETE FROM meet_pdf_results
                WHERE ct_meet_id IN (
                    SELECT ct_meet_id FROM meet_pdf_cache
                    WHERE pdf_url IS NULL OR pdf_url = ''
                )
            """)
            conn.execute("""
                DELETE FROM meet_pdf_swimmers
                WHERE ct_meet_id IN (
                    SELECT ct_meet_id FROM meet_pdf_cache
                    WHERE pdf_url IS NULL OR pdf_url = ''
                )
            """)
            conn.execute("""
                DELETE FROM meet_pdf_cache
                WHERE pdf_url IS NULL OR pdf_url = ''
            """)
    else:
        db.reset_pdf_caches()
    db.reset_member_triangulation()

    # skip_autofill=True is for the Data tab "Wipe" button: caller wants
    # a clean slate to test manual PDF uploads, NOT to immediately
    # re-trigger CT Swim fetches that would repopulate the cache.
    started = False if skip_autofill else age_filler.start_autofill(force_all=True)
    return jsonify({'ok': True, 'auto_fill_started': started,
                    'kept_uploaded_pdfs': keep_uploaded})


@app.route('/api/admin/unmatched_meets', methods=['GET'])
@role_required('admin')
def api_unmatched_meets():
    """List meets that the auto-discovery couldn't match a PDF for.
    Coach uses this to find which meets need a manual URL entry."""
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT ct_meet_id, meet_name, start_date, end_date
            FROM meet_pdf_cache
            WHERE note IN ('no_pdf', 'no_meta', 'download_failed')
            ORDER BY start_date DESC, ct_meet_id
        """).fetchall()
    return jsonify({'meets': [dict(r) for r in rows]})


# ===== Data tab: parsed-PDF data view (admin only) =====

@app.route('/api/admin/data_tab/results', methods=['GET'])
@role_required('admin')
def api_data_tab_results():
    """Paginated view of every parsed swimmer-event-swim across all meets.

    Filters: name (matches first/last partial), team, course (Y/L),
    stroke (FREE/BACK/...), distance (int), date_from, date_to (ISO),
    age_min, age_max, gender (F/M).

    Pagination: page (1-indexed) + size (default 50, max 500).
    """
    args = request.args
    where = []
    params = []

    # Excel-style multi-select: any of these may carry a comma-joined
    # list of values from a header dropdown ('IVY,WRAT,LEHY'). We turn
    # it into a SQL IN (...) clause when there are multiple values, or
    # = when there's just one. Empty string means no filter.
    def _multi(col, raw, normalize=lambda v: v):
        if not raw:
            return
        vals = [normalize(v.strip()) for v in raw.split(',') if v.strip()]
        if not vals:
            return
        if len(vals) == 1:
            where.append(f"{col} = ?")
            params.append(vals[0])
        else:
            where.append(f"{col} IN ({','.join('?' * len(vals))})")
            params.extend(vals)

    name = (args.get('name') or '').strip().lower()
    if name:
        where.append("(LOWER(r.first_name) LIKE ? OR LOWER(r.last_name) LIKE ?)")
        params.extend([f'%{name}%', f'%{name}%'])
    _multi("UPPER(r.team)", args.get('team', ''),
           normalize=lambda v: v.upper())
    meet_id = (args.get('meet_id') or '').strip()
    if meet_id:
        where.append("r.ct_meet_id = ?")
        params.append(meet_id)
    _multi("r.course", args.get('course', ''),
           normalize=lambda v: v.upper())
    _multi("r.stroke", args.get('stroke', ''),
           normalize=lambda v: v.upper())
    _multi("r.distance", args.get('distance', ''),
           normalize=lambda v: int(v) if v.isdigit() else v)
    _multi("r.gender", args.get('gender', ''),
           normalize=lambda v: v.upper())
    # Relay rows are noisy for verification (no time, no rank, names
    # split awkwardly) — exclude by default. Pass include_relays=1 to
    # see them.
    if not (args.get('include_relays') in ('1', 'true', 'True')):
        where.append("(r.stroke IS NULL OR r.stroke NOT IN ('FREE_RELAY', 'MEDLEY_RELAY'))")
    age_min = args.get('age_min')
    if age_min and age_min.isdigit():
        where.append("r.age >= ?")
        params.append(int(age_min))
    age_max = args.get('age_max')
    if age_max and age_max.isdigit():
        where.append("r.age <= ?")
        params.append(int(age_max))
    date_from = (args.get('date_from') or '').strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_from):
        where.append("c.start_date >= ?")
        params.append(date_from)
    date_to = (args.get('date_to') or '').strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_to):
        where.append("c.start_date <= ?")
        params.append(date_to)

    try:
        page = max(1, int(args.get('page', '1')))
    except ValueError:
        page = 1
    try:
        size = min(500, max(1, int(args.get('size', '50'))))
    except ValueError:
        size = 50
    offset = (page - 1) * size

    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    with db.get_conn() as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS n FROM meet_pdf_results r "
            f"LEFT JOIN meet_pdf_cache c ON c.ct_meet_id = r.ct_meet_id "
            f"{where_sql}",
            params
        ).fetchone()
        total = int(total_row['n']) if total_row else 0
        rows = conn.execute(
            f"SELECT r.id, r.ct_meet_id, c.meet_name, c.start_date, "
            f"       r.first_name, r.last_name, r.age, r.gender, r.team, "
            f"       r.event_name, r.distance, r.stroke, r.course, r.time "
            f"FROM meet_pdf_results r "
            f"LEFT JOIN meet_pdf_cache c ON c.ct_meet_id = r.ct_meet_id "
            f"{where_sql} "
            f"ORDER BY c.start_date DESC, r.last_name, r.first_name, r.distance "
            f"LIMIT ? OFFSET ?",
            params + [size, offset]
        ).fetchall()
    return jsonify({
        'rows': [dict(r) for r in rows],
        'total': total,
        'page': page,
        'size': size,
        'pages': (total + size - 1) // size if total else 0,
    })


@app.route('/api/admin/data_tab/distinct', methods=['GET'])
@role_required('admin')
def api_data_tab_distinct():
    """Distinct values for one column, used by the Excel-style header
    filter dropdowns. Honors any currently-applied filters EXCEPT the
    one being filtered (so you can refine other columns and still see
    the full distinct list for this column).

    Query: column=<team|stroke|distance|course|gender|meet_id>
    """
    col_map = {
        'team': 'UPPER(r.team)',
        'stroke': 'r.stroke',
        'distance': 'r.distance',
        'course': 'r.course',
        'gender': 'r.gender',
        'meet_id': 'r.ct_meet_id',
    }
    col = (request.args.get('column') or '').lower()
    if col not in col_map:
        return jsonify({'error': f'unknown column: {col}'}), 400
    expr = col_map[col]

    # Honor cross-column filters but NOT the column we're listing.
    where, params = [], []

    def _multi(sql_col, raw, normalize=lambda v: v):
        if not raw:
            return
        vals = [normalize(v.strip()) for v in raw.split(',') if v.strip()]
        if not vals:
            return
        if len(vals) == 1:
            where.append(f"{sql_col} = ?")
            params.append(vals[0])
        else:
            where.append(f"{sql_col} IN ({','.join('?' * len(vals))})")
            params.extend(vals)

    args = request.args
    name = (args.get('name') or '').strip().lower()
    if name:
        where.append("(LOWER(r.first_name) LIKE ? OR LOWER(r.last_name) LIKE ?)")
        params.extend([f'%{name}%', f'%{name}%'])
    if col != 'team':
        _multi("UPPER(r.team)", args.get('team', ''),
               normalize=lambda v: v.upper())
    if col != 'meet_id':
        mid = (args.get('meet_id') or '').strip()
        if mid:
            where.append("r.ct_meet_id = ?")
            params.append(mid)
    if col != 'course':
        _multi("r.course", args.get('course', ''),
               normalize=lambda v: v.upper())
    if col != 'stroke':
        _multi("r.stroke", args.get('stroke', ''),
               normalize=lambda v: v.upper())
    if col != 'distance':
        _multi("r.distance", args.get('distance', ''),
               normalize=lambda v: int(v) if v.isdigit() else v)
    if col != 'gender':
        _multi("r.gender", args.get('gender', ''),
               normalize=lambda v: v.upper())
    if not (args.get('include_relays') in ('1', 'true', 'True')):
        where.append("(r.stroke IS NULL OR r.stroke NOT IN ('FREE_RELAY', 'MEDLEY_RELAY'))")
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''

    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT {expr} AS value, COUNT(*) AS n FROM meet_pdf_results r "
            f"LEFT JOIN meet_pdf_cache c ON c.ct_meet_id = r.ct_meet_id "
            f"{where_sql} "
            f"GROUP BY {expr} "
            f"ORDER BY {expr}",
            params
        ).fetchall()
    return jsonify({
        'column': col,
        'values': [{'value': r['value'], 'count': r['n']}
                   for r in rows if r['value'] is not None],
    })


@app.route('/api/admin/data_tab/summary', methods=['GET'])
@role_required('admin')
def api_data_tab_summary():
    """Top-level Data tab counts + per-meet diagnostics.

    Returns:
      meets_total, meets_parsed, meets_no_pdf, meets_failed,
      results_total, swimmers_total,
      recent_meets[]: per-meet diagnostics (PDFs scanned, rows parsed,
                      unmatched-line samples) sorted newest first.
    """
    with db.get_conn() as conn:
        cache_rows = conn.execute("""
            SELECT ct_meet_id, meet_name, start_date, end_date, pdf_url,
                   parsed_at, note, parser_version, parse_diagnostics
            FROM meet_pdf_cache
            ORDER BY (start_date IS NULL), start_date DESC, ct_meet_id DESC
            LIMIT 500
        """).fetchall()
        meets_total = conn.execute(
            "SELECT COUNT(*) AS n FROM meet_pdf_cache"
        ).fetchone()['n']
        meets_parsed = conn.execute(
            "SELECT COUNT(*) AS n FROM meet_pdf_cache WHERE parsed_at IS NOT NULL"
        ).fetchone()['n']
        meets_no_pdf = conn.execute(
            "SELECT COUNT(*) AS n FROM meet_pdf_cache WHERE note = 'no_pdf'"
        ).fetchone()['n']
        meets_failed = conn.execute(
            "SELECT COUNT(*) AS n FROM meet_pdf_cache "
            "WHERE note IS NOT NULL AND note NOT IN ('no_pdf')"
        ).fetchone()['n']
        results_total = conn.execute(
            "SELECT COUNT(*) AS n FROM meet_pdf_results"
        ).fetchone()['n']
        swimmers_total = conn.execute(
            "SELECT COUNT(DISTINCT name_key) AS n FROM meet_pdf_results"
        ).fetchone()['n']

    recent = []
    for r in cache_rows:
        d = dict(r)
        diag = None
        if d.get('parse_diagnostics'):
            try:
                diag = json.loads(d['parse_diagnostics'])
            except (ValueError, TypeError):
                diag = None
        d['parse_diagnostics'] = diag
        recent.append(d)

    return jsonify({
        'totals': {
            'meets_total': meets_total,
            'meets_parsed': meets_parsed,
            'meets_no_pdf': meets_no_pdf,
            'meets_failed': meets_failed,
            'results_total': results_total,
            'swimmers_total': swimmers_total,
        },
        'recent_meets': recent,
    })


@app.route('/api/admin/data_tab/meets', methods=['GET'])
@role_required('admin')
def api_data_tab_meets():
    """Lightweight list of all known meets (id + name + date) for the
    add-row dropdown. Newest first."""
    return jsonify({'meets': db.list_meet_pdf_meets()})


@app.route('/api/admin/data_tab/row', methods=['POST'])
@role_required('admin')
def api_data_tab_row_add():
    """Manually add one Data-tab row.

    POST JSON: {ct_meet_id, first_name, last_name, age, gender, team,
                event_name, distance, stroke, course, time}
    Required: ct_meet_id, first_name, last_name. Empty strings → NULL.
    """
    payload = request.get_json(silent=True) or {}
    try:
        row = db.insert_meet_pdf_result(payload)
    except ValueError as e:
        return jsonify({'error': 'invalid', 'message': str(e)}), 400
    return jsonify({'ok': True, 'row': row})


@app.route('/api/admin/data_tab/row/<int:row_id>', methods=['PATCH'])
@role_required('admin')
def api_data_tab_row_update(row_id):
    """Update one Data-tab row. Body carries only the fields to change."""
    payload = request.get_json(silent=True) or {}
    try:
        row = db.update_meet_pdf_result(row_id, payload)
    except ValueError as e:
        return jsonify({'error': 'invalid', 'message': str(e)}), 400
    if row is None:
        return jsonify({'error': 'not_found'}), 404
    return jsonify({'ok': True, 'row': row})


@app.route('/api/admin/data_tab/row/<int:row_id>', methods=['DELETE'])
@role_required('admin')
def api_data_tab_row_delete(row_id):
    """Delete one Data-tab row by id."""
    ok = db.delete_meet_pdf_result(row_id)
    if not ok:
        return jsonify({'error': 'not_found'}), 404
    return jsonify({'ok': True})


@app.route('/api/admin/register_meet_pdf', methods=['POST'])
@role_required('admin')
def api_register_meet_pdf():
    """Manually link a CT Swim meet to its result PDF when it isn't on
    Results.aspx (e.g. championship meets that get filed under a separate
    /CT_Age_Groups/ subdirectory).

    POST JSON: {"meet_id": "7102", "pdf_url": "/Customer-Content/..."}

    Side effects: writes the URL to meet_pdf_cache, downloads + parses
    the PDF, populates meet_pdf_swimmers, and clears note='no_pdf' so the
    next age-fill run picks it up.
    """
    import ct_pdf as _ctp
    from datetime import datetime, timezone
    body = request.json or {}
    meet_id = (body.get('meet_id') or '').strip()
    pdf_url = (body.get('pdf_url') or '').strip()
    if not meet_id or not pdf_url:
        return jsonify({'error': 'meet_id and pdf_url required'}), 400

    pdf_bytes = _ctp.download_pdf(pdf_url)
    if not pdf_bytes:
        return jsonify({'error': 'PDF download failed', 'pdf_url': pdf_url}), 502

    try:
        rows, diag = _ctp.parse_results_pdf(pdf_bytes, return_diagnostics=True)
    except Exception as e:
        return jsonify({'error': f'parse failed: {type(e).__name__}: {e}'}), 500

    # Save to cache + swimmers (stamp the current parser version so this
    # row stays valid until the parser code changes meaningfully).
    diag_blob = json.dumps({'pdfs': [{'url': pdf_url,
                                      'rows': len(rows),
                                      'total_lines': diag['total_lines'],
                                      'unmatched_sample': diag['unmatched_sample']}]})
    db.save_meet_cache(
        meet_id, pdf_url=pdf_url,
        parsed_at=datetime.now(timezone.utc).isoformat(),
        note=None,
        parser_version=_ctp.PARSER_VERSION,
        parse_diagnostics=diag_blob,
    )
    db.save_meet_pdf_swimmers(meet_id, rows)

    # How many of OUR roster swimmers were found in this PDF? Tells the
    # admin whether the upload helps anyone.
    name_keys = set()
    with db.get_conn() as conn:
        members = conn.execute(
            "SELECT first_name, last_name FROM team_members"
        ).fetchall()
        for m in members:
            name_keys.add(_ctp.normalize_name(m['first_name'], m['last_name']))
    matched_team_members = sum(
        1 for r in rows if r.get('name_key') in name_keys
    )

    # Auto-trigger age-fill so the new PDF data flows into team_members
    # immediately. Use force_all=True so already-resolved swimmers get
    # re-checked with the new data (their birth window might tighten).
    auto_fill_started = False
    try:
        auto_fill_started = age_filler.start_autofill(force_all=True)
    except Exception:
        pass

    return jsonify({
        'ok': True,
        'meet_id': meet_id,
        'pdf_url': pdf_url,
        'pdf_size': len(pdf_bytes),
        'parsed_rows': len(rows),
        'matched_team_members': matched_team_members,
        'auto_fill_started': auto_fill_started,
    })


@app.route('/api/admin/upload_pdf', methods=['POST'])
@role_required('admin')
def api_upload_pdf():
    """Direct PDF upload (bypasses ctswim.org URL fetch).

    Form fields:
      pdf            (file, required) — Hy-Tek result PDF
      ct_meet_id     (str, optional)  — explicit CT Swim meet id; if
                                        omitted we auto-detect from the
                                        PDF header or synthesize one
      dry_run        ('1' or '0')     — preview only, no DB write
      force          ('1' or '0')     — bypass duplicate-detection
                                        guards (re-import same hash,
                                        replace already-parsed meet)

    Edge cases handled:
      - Same PDF re-uploaded: SHA-256 of bytes matches an existing
        cache row → 409 unless force=1
      - Different PDF for same already-parsed meet (corrected exports):
        ct_meet_id matches a cache row already at current parser_version
        → 409 unless force=1
      - Multiple PDFs for one meet (Senior + AG sessions): admin
        provides the same ct_meet_id; rows APPEND (no DELETE)
      - Out-of-CT-Swim meets: ct_meet_id omitted; we extract meet name
        and date from the PDF header and synthesize 'manual_<hash8>'
      - Wrong file (not a Hy-Tek PDF): parser returns 0 swimmer rows;
        we surface 'no swimmer rows found' without writing
      - Encrypted/corrupt PDFs: pypdf raises; surfaced as 422
      - Oversized files: Flask's MAX_CONTENT_LENGTH (10 MB) returns 413
        before this handler runs
    """
    import ct_pdf as _ctp
    import hashlib
    from datetime import datetime, timezone

    f = request.files.get('pdf')
    if not f or not f.filename:
        return jsonify({'error': 'pdf file required'}), 400
    pdf_bytes = f.read()
    if not pdf_bytes:
        return jsonify({'error': 'uploaded file is empty'}), 400
    if not pdf_bytes.startswith(b'%PDF'):
        return jsonify({'error': 'file does not look like a PDF (missing %PDF header)'}), 400

    pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()[:16]
    dry_run = request.form.get('dry_run') in ('1', 'true', 'True')
    force = request.form.get('force') in ('1', 'true', 'True')
    ct_meet_id_input = (request.form.get('ct_meet_id') or '').strip()

    # ===== Edge case 1: exact-bytes duplicate =====
    dup = db.lookup_pdf_by_hash(pdf_hash)
    if dup and not force:
        return jsonify({
            'error': 'duplicate',
            'message': 'This exact PDF has already been ingested.',
            'duplicate_of': dup,
            'pdf_hash': pdf_hash,
        }), 409

    # ===== Parse the PDF =====
    try:
        rows, diag = _ctp.parse_results_pdf(pdf_bytes, return_diagnostics=True)
    except Exception as e:
        return jsonify({'error': f'parse failed: {type(e).__name__}: {e}',
                        'pdf_hash': pdf_hash}), 422

    # ===== Edge case 2: zero rows (wrong file format) =====
    if not rows:
        return jsonify({
            'error': 'no_rows',
            'message': 'No swimmer rows found. Is this a Hy-Tek result PDF?',
            'pdf_hash': pdf_hash,
            'diagnostics': diag,
        }), 422

    # ===== Resolve ct_meet_id =====
    meta = _ctp.extract_meet_metadata_from_pdf(pdf_bytes)
    detected_name = meta.get('meet_name') or ''
    detected_start = meta.get('start_date')
    detected_end = meta.get('end_date') or detected_start
    start_iso = detected_start.isoformat() if detected_start else None
    end_iso = detected_end.isoformat() if detected_end else None

    auto_attached_meet = None
    ct_meet_id = ct_meet_id_input
    if not ct_meet_id:
        # Try to attach to a known meet by (name, start_date)
        candidate = db.find_meet_by_name_date(detected_name, start_iso)
        if candidate:
            ct_meet_id = candidate['ct_meet_id']
            auto_attached_meet = candidate
        else:
            # Synthesize a stable id from the file hash
            ct_meet_id = f'manual_{pdf_hash[:8]}'

    # ===== Edge case 3: meet already parsed at current parser version =====
    existing_cache = db.get_meet_cache(ct_meet_id)
    already_current = (
        existing_cache
        and existing_cache.get('parsed_at')
        and (existing_cache.get('parser_version') or 0) >= _ctp.PARSER_VERSION
    )
    same_pdf_already_in_meet = False
    if existing_cache and existing_cache.get('pdf_hashes'):
        existing_hashes = set(h.strip() for h in existing_cache['pdf_hashes'].split(',') if h.strip())
        same_pdf_already_in_meet = pdf_hash in existing_hashes

    # If user supplied a ct_meet_id that's already current AND this is a
    # NEW PDF (different hash), assume they're adding a sibling session
    # and append. If it's the SAME hash, that's a duplicate (caught above
    # via the global hash check, but defend belt-and-suspenders).
    will_append_to_existing = bool(
        already_current
        and not same_pdf_already_in_meet
        and existing_cache and existing_cache.get('parsed_at')
    )
    will_replace_existing = bool(
        already_current and same_pdf_already_in_meet
    )

    if will_replace_existing and not force:
        return jsonify({
            'error': 'already_parsed',
            'message': 'This PDF has already been parsed for this meet at the current parser version.',
            'ct_meet_id': ct_meet_id,
            'existing': existing_cache,
            'pdf_hash': pdf_hash,
        }), 409

    # ===== Compute roster match count =====
    name_keys = set()
    with db.get_conn() as conn:
        members = conn.execute(
            "SELECT first_name, last_name FROM team_members"
        ).fetchall()
        for m in members:
            name_keys.add(_ctp.normalize_name(m['first_name'], m['last_name']))
    matched_roster = sum(1 for r in rows if r.get('name_key') in name_keys)
    sample_swimmers = [
        {'first': r['first'], 'last': r['last'], 'age': r['age'],
         'team': r.get('team', ''), 'gender': r.get('gender', '')}
        for r in rows[:8]
    ]

    preview = {
        'pdf_hash': pdf_hash,
        'pdf_size': len(pdf_bytes),
        'filename': f.filename,
        'detected_meet_name': detected_name,
        'detected_start_date': start_iso,
        'detected_end_date': end_iso,
        'ct_meet_id': ct_meet_id,
        'ct_meet_id_was_synthesized': ct_meet_id.startswith('manual_'),
        'auto_attached_meet': auto_attached_meet,
        'parsed_rows': len(rows),
        'parsed_unique_swimmers': len({r['name_key'] for r in rows}),
        'matched_roster_swimmers': matched_roster,
        'will_append_to_existing': will_append_to_existing,
        'existing_cache': existing_cache,
        'sample_swimmers': sample_swimmers,
        'parser_diagnostics': diag,
    }

    if dry_run:
        preview['dry_run'] = True
        return jsonify(preview)

    # ===== Commit =====
    # IMPORTANT: meet_pdf_cache row must exist BEFORE meet_pdf_swimmers /
    # meet_pdf_results inserts because of the foreign-key constraint on
    # ct_meet_id. age_filler's flow does the same upsert-cache-first.
    save_mode = 'append' if will_append_to_existing else 'replace'
    new_hashes = pdf_hash
    if existing_cache and existing_cache.get('pdf_hashes'):
        existing = [h.strip() for h in existing_cache['pdf_hashes'].split(',') if h.strip()]
        if pdf_hash not in existing:
            new_hashes = ','.join(existing + [pdf_hash])
        else:
            new_hashes = existing_cache['pdf_hashes']
    new_pdf_url_token = f'upload:{f.filename}'
    if existing_cache and existing_cache.get('pdf_url') and save_mode == 'append':
        existing_urls = [u.strip() for u in existing_cache['pdf_url'].split(',') if u.strip()]
        if new_pdf_url_token not in existing_urls:
            new_pdf_url = ','.join(existing_urls + [new_pdf_url_token])
        else:
            new_pdf_url = existing_cache['pdf_url']
    else:
        new_pdf_url = new_pdf_url_token

    diag_blob = json.dumps({'pdfs': [{
        'url': new_pdf_url_token,
        'rows': len(rows),
        'total_lines': diag['total_lines'],
        'unmatched_sample': diag['unmatched_sample'],
    }]})

    db.save_meet_cache(
        ct_meet_id,
        meet_name=(existing_cache.get('meet_name')
                   if existing_cache and existing_cache.get('meet_name')
                   else detected_name) or 'Manually uploaded meet',
        start_date=(existing_cache.get('start_date')
                    if existing_cache and existing_cache.get('start_date')
                    else start_iso),
        end_date=(existing_cache.get('end_date')
                  if existing_cache and existing_cache.get('end_date')
                  else end_iso),
        pdf_url=new_pdf_url,
        parsed_at=datetime.now(timezone.utc).isoformat(),
        note=None,
        parser_version=_ctp.PARSER_VERSION,
        parse_diagnostics=diag_blob,
        pdf_hashes=new_hashes,
    )
    db.save_meet_pdf_swimmers(ct_meet_id, rows, mode=save_mode)

    db.log_sync(None, 'pdf_upload', 'ok',
                f"{f.filename} → {ct_meet_id} ({len(rows)} rows, "
                f"{matched_roster} on roster) mode={save_mode}")

    preview['ok'] = True
    preview['saved'] = True
    preview['save_mode'] = save_mode
    return jsonify(preview)


@app.route('/api/admin/diagnose_member', methods=['GET'])
@role_required('admin')
def api_diagnose_member():
    """Show full age-fill state for one swimmer in a single response.

    Query: GET /api/admin/diagnose_member?name=Chilukuri
    """
    import ct_pdf as _ctp
    name_q = (request.args.get('name') or '').strip()
    if not name_q:
        return jsonify({'error': 'name= query required'}), 400

    out = {'query': name_q}

    # 1. team_member row(s) matching name
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, first_name, last_name, gender, birth_year, "
            "birth_month, age_observed, age_window_days, age_synced_at, "
            "ct_id FROM team_members WHERE last_name LIKE ? OR first_name LIKE ?",
            (f"%{name_q}%", f"%{name_q}%")
        ).fetchall()
    out['team_members'] = [dict(r) for r in rows]
    if not rows:
        return jsonify(out)

    member = dict(rows[0])
    out['target'] = f"{member['first_name']} {member['last_name']}"
    name_key = _ctp.normalize_name(member['first_name'], member['last_name'])
    out['name_key'] = name_key

    # 2. event_history meet_id distribution
    with db.get_conn() as conn:
        eh = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN ct_meet_id IS NOT NULL AND ct_meet_id != '' THEN 1 ELSE 0 END) AS with_mid "
            "FROM event_history WHERE swimmer_ct_id = ?",
            (member.get('ct_id'),)
        ).fetchone()
    out['event_history'] = dict(eh)

    # 3. distinct meets we know about for this swimmer
    distinct_meets = db.get_member_meet_history(member['id'])
    out['distinct_meets_count'] = len(distinct_meets)

    # 4. meet_pdf_cache state for those meets
    if distinct_meets:
        ids = [m['ct_meet_id'] for m in distinct_meets]
        placeholders = ','.join('?' * len(ids))
        with db.get_conn() as conn:
            cache_rows = conn.execute(
                f"SELECT ct_meet_id, meet_name, pdf_url, parsed_at, note, "
                f"parser_version FROM meet_pdf_cache "
                f"WHERE ct_meet_id IN ({placeholders})", ids
            ).fetchall()
        cache_dict = {r['ct_meet_id']: dict(r) for r in cache_rows}
        out['cache_summary'] = {
            'total_meets_for_swimmer': len(ids),
            'in_cache': len(cache_dict),
            'parsed': sum(1 for r in cache_rows if r['parsed_at']),
            'no_pdf': sum(1 for r in cache_rows if r['note'] == 'no_pdf'),
            'no_meta': sum(1 for r in cache_rows if r['note'] == 'no_meta'),
            'other_note': sum(1 for r in cache_rows
                              if r['note'] and r['note'] not in ('no_pdf', 'no_meta')),
        }
        import ct_pdf as _ctp_v
        out['current_parser_version'] = _ctp_v.PARSER_VERSION
        out['per_meet'] = []
        for r in cache_rows:
            urls = (r['pdf_url'] or '').split(',') if r['pdf_url'] else []
            out['per_meet'].append({
                'ct_meet_id': r['ct_meet_id'],
                'meet_name': r['meet_name'],
                'note': r['note'],
                'parsed_at': r['parsed_at'],
                'parser_version': r['parser_version'],
                'num_pdfs': len([u for u in urls if u.strip()]),
                'pdf_urls': [u.strip() for u in urls if u.strip()],
            })

    # 5. swimmer rows in meet_pdf_swimmers (the actual triangulation source)
    with db.get_conn() as conn:
        sw_rows = conn.execute(
            "SELECT ct_meet_id, age, gender, team FROM meet_pdf_swimmers "
            "WHERE name_key = ? ORDER BY ct_meet_id",
            (name_key,)
        ).fetchall()
    out['parsed_swimmer_rows'] = [dict(r) for r in sw_rows]
    out['parsed_swimmer_count'] = len(sw_rows)
    if sw_rows:
        ages = sorted({r['age'] for r in sw_rows})
        genders = sorted({r['gender'] or '' for r in sw_rows})
        teams = sorted({r['team'] or '' for r in sw_rows})
        out['parsed_summary'] = {'ages': ages, 'genders': genders, 'teams': teams}

    return jsonify(out)


@app.route('/api/admin/diagnose_parser', methods=['GET'])
@role_required('admin')
def api_diagnose_parser():
    """Run pypdf against a known-good PDF and return raw extracted text +
    versions. If text comes back empty here, pypdf's extract_text is the
    problem; if text comes back populated but our regex finds 0 matches,
    the regex is the problem."""
    import sys as _sys
    import requests as _req
    import io as _io
    import ct_pdf as _ctp

    out = {
        'python_version': _sys.version,
    }

    # pypdf version
    try:
        import pypdf as _pypdf
        out['pypdf_version'] = getattr(_pypdf, '__version__', 'unknown')
    except Exception as e:
        out['pypdf_version'] = f"import failed: {e}"
        return jsonify(out)

    # Either ?meet_id=NNNN to use the cached meet's PDF, or ?url=... to
    # test any arbitrary URL, or fall back to the LEHY 2025 known-good.
    meet_id = (request.args.get('meet_id') or '').strip()
    custom_url = (request.args.get('url') or '').strip()
    if meet_id:
        cache_row = db.get_meet_cache(meet_id)
        if not cache_row or not cache_row.get('pdf_url'):
            return jsonify({**out, 'error':
                f'meet_id {meet_id} not in meet_pdf_cache or has no pdf_url'})
        pdf_url = cache_row['pdf_url']
        # pdf_url may be a comma-joined list of sibling PDFs; pick first.
        if ',' in pdf_url:
            pdf_url = pdf_url.split(',', 1)[0]
        if not pdf_url.startswith('http'):
            pdf_url = 'https://www.ctswim.org' + pdf_url
        out['source'] = f'meet_id {meet_id}'
    elif custom_url:
        pdf_url = custom_url
        out['source'] = 'custom url'
    else:
        pdf_url = ('https://www.ctswim.org/Customer-Content/www/Meets/'
                   'LC2025/Regionals/071925lehy12u_results.pdf')
        out['source'] = 'LEHY 2025 (default)'
    out['pdf_url'] = pdf_url

    r = _req.get(pdf_url, timeout=30, headers={'User-Agent': _ctp.USER_AGENT})
    out['pdf_size'] = len(r.content)
    out['pdf_is_pdf'] = r.content[:8].decode('ascii', errors='replace')

    # Run pypdf directly
    try:
        from pypdf import PdfReader
        reader = PdfReader(_io.BytesIO(r.content))
        out['pdf_pages'] = len(reader.pages)
        # Extract all pages so we can find specific names anywhere in the doc
        all_text = '\n'.join((p.extract_text() or '') for p in reader.pages)
        out['total_text_len'] = len(all_text)
        out['first_page_head'] = (reader.pages[0].extract_text() or '')[:1500]

        # If a name= filter is given, dump every line that contains it
        name = (request.args.get('name') or '').strip()
        if name:
            lines = [ln for ln in all_text.split('\n') if name.lower() in ln.lower()]
            out['lines_matching_name'] = lines[:20]

        # Count regex matches across the whole doc
        a_matches = _ctp._PDF_ROW_A_RE.findall(all_text)
        b_matches = _ctp._PDF_ROW_B_RE.findall(all_text)
        out['format_a_matches'] = len(a_matches)
        out['format_b_matches'] = len(b_matches)
        # Show a sample to verify the regex shape
        out['sample_a_match'] = a_matches[0] if a_matches else None
        out['sample_b_match'] = b_matches[0] if b_matches else None

        # Run the actual parser to confirm what it produces
        rows = _ctp.parse_results_pdf(r.content)
        out['parsed_total'] = len(rows)
        out['parsed_ivy'] = sum(1 for x in rows if x['team'] == 'IVY')
    except Exception as e:
        out['parse_error'] = f"{type(e).__name__}: {e}"

    return jsonify(out)


@app.route('/api/admin/diagnose_index', methods=['GET'])
@role_required('admin')
def api_diagnose_index():
    """Show what ctswim.org/Meets/Results.aspx looks like from Render's
    view, AND whether direct PDF downloads work (Cloudflare often
    challenges HTML pages but allows static files)."""
    import requests as _req
    import re as _re
    import ct_pdf
    out = {}

    # Test 1: HTML index page
    page = request.args.get('page', '1')
    url = f'https://www.ctswim.org/Meets/Results.aspx?page={page}'
    out['index_test'] = {'url': url}
    try:
        r = _req.get(url, timeout=15, headers={'User-Agent': ct_pdf.USER_AGENT})
        out['index_test']['status_code'] = r.status_code
        out['index_test']['body_length'] = len(r.text)
        out['index_test']['cf_mitigated'] = r.headers.get('cf-mitigated', '')
        matches = _re.findall(r'href="([^"]*_results\.pdf)"', r.text, _re.IGNORECASE)
        out['index_test']['pdf_matches'] = len(matches)
    except Exception as e:
        out['index_test']['error'] = f"{type(e).__name__}: {e}"

    # Test 2: Direct PDF download (known LEHY 2025 12U Regional)
    pdf_url = ('https://www.ctswim.org/Customer-Content/www/Meets/'
               'LC2025/Regionals/071925lehy12u_results.pdf')
    out['pdf_test'] = {'url': pdf_url}
    try:
        r = _req.get(pdf_url, timeout=30, headers={'User-Agent': ct_pdf.USER_AGENT})
        out['pdf_test']['status_code'] = r.status_code
        out['pdf_test']['size_bytes'] = len(r.content)
        out['pdf_test']['cf_mitigated'] = r.headers.get('cf-mitigated', '')
        out['pdf_test']['is_pdf'] = r.content[:4] == b'%PDF'
        out['pdf_test']['content_type'] = r.headers.get('content-type', '')
    except Exception as e:
        out['pdf_test']['error'] = f"{type(e).__name__}: {e}"

    return jsonify(out)


@app.route('/api/admin/diagnose_pipeline', methods=['GET'])
@role_required('admin')
def api_diagnose_pipeline():
    """Trace one meet through the full age-fill pipeline and return JSON
    showing what each step produced. Use to debug 'Not found' issues.

    Query params:
      ct_id    — swimmer's CT Swim id (required)
      meet_id  — the m= number from a SwimmerAtMeet URL (required)
      reset    — '1' to clear all 'no_pdf' cache entries before running
    """
    import ct_pdf
    ct_id = request.args.get('ct_id', '').strip()
    meet_id = request.args.get('meet_id', '').strip()
    if not ct_id or not meet_id:
        return jsonify({'error': 'ct_id and meet_id required'}), 400

    out = {'ct_id': ct_id, 'meet_id': meet_id, 'steps': {}}

    if request.args.get('reset') == '1':
        with db.get_conn() as conn:
            n = conn.execute(
                "DELETE FROM meet_pdf_cache WHERE note = 'no_pdf'"
            ).rowcount
        out['cache_cleared'] = n

    # Step 1: SwimmerAtMeet metadata
    try:
        meta = ct_pdf.fetch_swimmer_at_meet(ct_id, meet_id)
        out['steps']['1_swimmer_at_meet'] = {
            'success': meta is not None,
            'meet_name': meta.get('meet_name') if meta else None,
            'start_date': meta['start_date'].isoformat() if meta and meta.get('start_date') else None,
            'end_date': meta['end_date'].isoformat() if meta and meta.get('end_date') else None,
        }
    except Exception as e:
        out['steps']['1_swimmer_at_meet'] = {'success': False, 'error': str(e)}
        return jsonify(out)

    if not meta:
        return jsonify(out)

    # Step 2: Load index from data/meet_index.json (same path auto-fill uses)
    try:
        import age_filler as _af
        index = _af._load_index_from_json()
        out['steps']['2_index_scrape'] = {
            'success': bool(index),
            'count': len(index),
            'sample_urls': [p['url'] for p in index[:3]],
            'source': 'data/meet_index.json',
        }
        # If the bundled JSON is missing, fall through to live scrape so
        # we can tell whether ctswim.org is reachable at all.
        if not index:
            live = ct_pdf.scrape_results_index()
            out['steps']['2_index_scrape']['live_fallback_count'] = len(live)
            index = live
    except Exception as e:
        out['steps']['2_index_scrape'] = {'success': False, 'error': str(e)}
        return jsonify(out)

    # Step 3: Match PDF
    try:
        pdf = ct_pdf.find_pdf_for_meet(
            meta.get('meet_name', ''),
            meta.get('start_date'),
            meta.get('end_date'),
            index,
        )
        out['steps']['3_find_pdf'] = {
            'success': pdf is not None,
            'pdf_url': pdf['url'] if pdf else None,
        }
    except Exception as e:
        out['steps']['3_find_pdf'] = {'success': False, 'error': str(e)}
        return jsonify(out)

    if not pdf:
        return jsonify(out)

    # Step 4: Download PDF
    try:
        body = ct_pdf.download_pdf(pdf['url'])
        out['steps']['4_download'] = {
            'success': body is not None,
            'size_bytes': len(body) if body else 0,
        }
    except Exception as e:
        out['steps']['4_download'] = {'success': False, 'error': str(e)}
        return jsonify(out)

    if not body:
        return jsonify(out)

    # Step 5: Parse PDF
    try:
        rows = ct_pdf.parse_results_pdf(body)
        # Look for our team's swimmers in the parsed rows
        ivy_rows = [r for r in rows if r['team'] == 'IVY']
        out['steps']['5_parse'] = {
            'success': True,
            'total_rows': len(rows),
            'ivy_rows': len(ivy_rows),
            'sample_ivy': [
                {'name': f"{r['first']} {r['last']}", 'age': r['age'],
                 'gender': r['gender'], 'name_key': r['name_key']}
                for r in ivy_rows[:5]
            ],
        }
    except Exception as e:
        out['steps']['5_parse'] = {
            'success': False,
            'error': f"{type(e).__name__}: {e}",
        }

    return jsonify(out)


@app.route('/api/team_members/autofill_ages/status', methods=['GET'])
@role_required('admin')
def api_autofill_ages_status():
    return jsonify(age_filler.get_status())


@app.route('/api/team_members/autofill_ages/cancel', methods=['POST'])
@role_required('admin')
def api_autofill_ages_cancel():
    age_filler.cancel_job()
    return jsonify({'ok': True})


@app.route('/api/dashboard', methods=['GET'])
@role_required('admin', 'coach')
def api_dashboard():
    """Return team_members with their cached best_times.
    Standards comparison happens client-side using existing helpers.
    """
    members = db.list_team_members_with_times()
    return jsonify({'members': members, 'count': len(members)})


@app.route('/api/team_members', methods=['POST'])
@role_required('admin', 'coach')
def api_create_team_member():
    data = request.json or {}
    first = (data.get('first_name') or '').strip()
    last = (data.get('last_name') or '').strip()
    if not first or not last:
        return jsonify({'error': 'First and last name are required'}), 400

    by, bm = _split_birth_month(data)
    member_id = db.create_team_member(
        first_name=first,
        last_name=last,
        roster=data.get('roster'),
        gender=data.get('gender'),
        birth_year=by,
        birth_month=bm,
        parent_email=data.get('parent_email'),
        notes=data.get('notes'),
    )
    return jsonify({'ok': True, 'id': member_id, 'member': db.get_team_member(member_id)})


@app.route('/api/team_members/<int:member_id>', methods=['PUT', 'PATCH'])
@role_required('admin', 'coach')
def api_update_team_member(member_id):
    existing = db.get_team_member(member_id)
    if not existing:
        return jsonify({'error': 'Member not found'}), 404
    data = request.json or {}
    update_fields = {
        k: data.get(k) for k in ('first_name', 'last_name', 'roster', 'gender',
                                 'parent_email', 'notes')
        if k in data
    }
    by, bm = _split_birth_month(data)
    if by is not None:
        update_fields['birth_year'] = by
    if bm is not None:
        update_fields['birth_month'] = bm
    db.update_team_member(member_id, **update_fields)
    return jsonify({'ok': True, 'member': db.get_team_member(member_id)})


@app.route('/api/team_members/<int:member_id>', methods=['DELETE'])
@role_required('admin', 'coach')
def api_delete_team_member(member_id):
    existing = db.get_team_member(member_id)
    if not existing:
        return jsonify({'error': 'Member not found'}), 404
    db.delete_team_member(member_id)
    return jsonify({'ok': True})


def _split_birth_month(data):
    """Accept either separate birth_year/birth_month fields or a combined
    'birth_month_str' shaped 'YYYY-MM' (the value an <input type=month> sends).
    Returns (year, month) — either may be None if absent/invalid.
    """
    by = data.get('birth_year')
    bm = data.get('birth_month')
    s = data.get('birth_month_str')
    if (not by or not bm) and s:
        parts = str(s).split('-')
        if len(parts) >= 2:
            by = by or parts[0]
            bm = bm or parts[1]
    return by, bm


# ===== API: Standards (read by anyone logged in; edit by admin/coach) =====

@app.route('/api/standards', methods=['GET'])
@login_required
def api_get_standards():
    return jsonify(standards_store.load())


@app.route('/api/standards/program', methods=['POST'])
@role_required('admin', 'coach')
def api_create_program():
    data = request.json or {}
    program_id = (data.get('program_id') or '').strip()
    if not program_id:
        return jsonify({'error': 'program_id is required'}), 400
    try:
        prog = standards_store.create_program(program_id, data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True, 'program_id': program_id, 'program': prog})


@app.route('/api/standards/program/<program_id>', methods=['DELETE'])
@role_required('admin', 'coach')
def api_delete_program(program_id):
    full = standards_store.load()
    if program_id not in full.get('programs', {}):
        return jsonify({'error': 'Unknown program'}), 404
    standards_store.delete_program(program_id)
    return jsonify({'ok': True})


@app.route('/api/standards/program/<program_id>/group', methods=['POST'])
@role_required('admin', 'coach')
def api_add_group(program_id):
    data = request.json or {}
    group_name = (data.get('group_name') or '').strip()
    try:
        prog = standards_store.add_group(program_id, group_name)
    except KeyError:
        return jsonify({'error': 'Unknown program'}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True, 'program': prog})


@app.route('/api/standards/program/<program_id>/group/<path:group>', methods=['DELETE'])
@role_required('admin', 'coach')
def api_delete_group(program_id, group):
    full = standards_store.load()
    prog = full.get('programs', {}).get(program_id)
    if not prog or group not in prog.get('groups', {}):
        return jsonify({'error': 'Unknown program/group'}), 404
    standards_store.delete_group(program_id, group)
    return jsonify({'ok': True})


@app.route('/api/standards/program/<program_id>/metadata', methods=['PATCH'])
@role_required('admin', 'coach')
def api_update_program_metadata(program_id):
    data = request.json or {}
    try:
        prog = standards_store.update_program_metadata(program_id, data)
    except KeyError:
        return jsonify({'error': 'Unknown program'}), 404
    return jsonify({'ok': True, 'program': prog})


@app.route('/api/standards/program/<program_id>/group/<path:group>/event/<int:event_index>',
           methods=['PATCH'])
@role_required('admin', 'coach')
def api_update_event(program_id, group, event_index):
    """Update a single event row.
    For non-USA: body = { gender_key: { course: time, ... }, ..., event_name?: str }
    For USA:     body = { times: [time per level], event_name?: str }
    """
    data = request.json or {}
    full = standards_store.load()
    if program_id not in full.get('programs', {}):
        return jsonify({'error': 'Unknown program'}), 404
    prog = full['programs'][program_id]
    if group not in prog.get('groups', {}):
        return jsonify({'error': 'Unknown group'}), 404

    grp = prog['groups'][group]
    if event_index < 0 or event_index >= len(grp.get('events', [])):
        return jsonify({'error': 'Event index out of range'}), 400

    # Optional rename
    if 'event_name' in data and data['event_name']:
        standards_store.rename_event(program_id, group, event_index, data['event_name'])

    if prog.get('multi_level'):
        times = data.get('times', [])
        if not isinstance(times, list) or len(times) != len(grp['levels']):
            return jsonify({'error': f"Expected list of {len(grp['levels'])} times"}), 400
        for li, t in enumerate(times):
            standards_store.update_usa_cell(program_id, group, event_index, li, t)
    else:
        for gender_key in prog.get('gender_keys', []):
            cell = data.get(gender_key) or {}
            for course in ['SCY', 'LCM']:
                if course in cell:
                    standards_store.update_event_time(program_id, group, gender_key, course,
                                                      event_index, cell[course])

    return jsonify({'ok': True, 'program': standards_store.load()['programs'][program_id]})


@app.route('/api/standards/program/<program_id>/group/<path:group>/event', methods=['POST'])
@role_required('admin', 'coach')
def api_add_event(program_id, group):
    """Add a new event. Body: { event_name: str, ...same payload shape as update }"""
    data = request.json or {}
    name = (data.get('event_name') or '').strip()
    if not name:
        return jsonify({'error': 'event_name is required'}), 400
    full = standards_store.load()
    if program_id not in full.get('programs', {}) or group not in full['programs'][program_id].get('groups', {}):
        return jsonify({'error': 'Unknown program/group'}), 404
    prog = full['programs'][program_id]

    if prog.get('multi_level'):
        # Map list of times to {level: time}
        times = data.get('times', [])
        levels = prog['groups'][group]['levels']
        payload = {levels[i]: times[i] if i < len(times) else '' for i in range(len(levels))}
    else:
        payload = {gk: data.get(gk) or {} for gk in prog.get('gender_keys', [])}

    new_idx = standards_store.add_event(program_id, group, name, payload)
    return jsonify({'ok': True, 'index': new_idx,
                    'program': standards_store.load()['programs'][program_id]})


@app.route('/api/standards/program/<program_id>/group/<path:group>/event/<int:event_index>',
           methods=['DELETE'])
@role_required('admin', 'coach')
def api_delete_event(program_id, group, event_index):
    full = standards_store.load()
    if program_id not in full.get('programs', {}) or group not in full['programs'][program_id].get('groups', {}):
        return jsonify({'error': 'Unknown program/group'}), 404
    try:
        standards_store.delete_event(program_id, group, event_index)
    except IndexError:
        return jsonify({'error': 'Event index out of range'}), 400
    return jsonify({'ok': True, 'program': standards_store.load()['programs'][program_id]})


@app.route('/api/batch/start', methods=['POST'])
@role_required('admin')
def batch_start():
    mode = request.json.get('mode', 'last_names')
    team_filter = request.json.get('team_filter', '').strip()

    if mode == 'last_names':
        raw = request.json.get('last_names', '')
        names = [n.strip() for n in raw.split('\n') if n.strip()] if isinstance(raw, str) else [str(n).strip() for n in raw if str(n).strip()]
        if not names:
            return jsonify({'error': 'Provide at least one last name'}), 400
        started = batch.start_batch_last_names(names, team_filter, ct_search_swimmers, ct_fetch_and_cache_swimmer)
    elif mode == 'team_code':
        team_code = request.json.get('team_code', '').strip()
        if not team_code:
            return jsonify({'error': 'Provide a team code'}), 400
        started = batch.start_batch_team_code(team_code, ct_search_swimmers, ct_fetch_and_cache_swimmer)
    elif mode == 'team_roster':
        only_new = bool(request.json.get('only_new'))
        team_members = db.list_team_members_with_times() if only_new else db.list_team_members()
        from pdf_parser import normalize_for_match
        included = []
        skipped_existing = 0
        for m in team_members:
            # When only_new is requested, skip swimmers who already have cached times.
            # An unmatched swimmer (no ct_id) is always included — they need the search.
            if only_new and m.get('ct_id') and (m.get('best_times') or []):
                skipped_existing += 1
                continue
            full_name = f"{m['first_name']} {m['last_name']}"
            included.append({
                'first_name': m['first_name'],
                'last_name': m['last_name'],
                'full_name': full_name,
                'search_key': m['last_name'].split()[0] if m['last_name'] else '',
                'match_key': normalize_for_match(full_name),
                'roster': m.get('roster') or '',
            })
        if not included:
            msg = ('All matched swimmers already have cached times. Uncheck '
                   '"Only newly added" to refresh everyone.') if only_new \
                  else 'Roster is empty. Add swimmers in the Roster tab first.'
            return jsonify({'error': msg}), 400
        started = batch.start_batch_member_directory(included, ct_search_swimmers, ct_fetch_and_cache_swimmer)
    else:
        return jsonify({'error': 'Invalid mode'}), 400

    if not started:
        return jsonify({'error': 'A batch job is already running. Cancel it first.'}), 409
    return jsonify({'ok': True, 'status': batch.get_status()})


@app.route('/api/batch/status', methods=['GET'])
@role_required('admin')
def batch_status():
    status = batch.get_status()
    status['recent_log'] = batch.get_recent_log(20)
    return jsonify(status)


@app.route('/api/batch/cancel', methods=['POST'])
@role_required('admin')
def batch_cancel():
    batch.cancel_job()
    return jsonify({'ok': True})


if __name__ == '__main__':
    app.run(debug=True, port=5000)

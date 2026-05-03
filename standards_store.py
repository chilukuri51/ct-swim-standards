"""Standards data store: JSON-file-backed, edited via API.

`data/standards.default.json` is the committed baseline.
`data/standards.json` is the working copy (gitignored), seeded from default
on first run. Admin/coach edits write to the working copy.
"""

import json
import os
import shutil
import threading
from datetime import datetime, timezone

import paths


# Default (committed) baseline always lives next to the code.
DEFAULT_PATH = os.path.join(paths.PROJECT_DATA_DIR, 'standards.default.json')
# Working copy goes on the persistent disk if one is mounted.
DATA_DIR = paths.DATA_DIR
WORKING_PATH = os.path.join(DATA_DIR, 'standards.json')

_lock = threading.Lock()


def ensure_seeded():
    """Copy default -> working copy if working doesn't exist yet."""
    if not os.path.exists(WORKING_PATH) and os.path.exists(DEFAULT_PATH):
        os.makedirs(DATA_DIR, exist_ok=True)
        shutil.copyfile(DEFAULT_PATH, WORKING_PATH)


def load() -> dict:
    """Load the working standards data."""
    ensure_seeded()
    if not os.path.exists(WORKING_PATH):
        return {'metadata': {}, 'programs': {}, 'conversion_factors': {}, 'whatif_events': {}}
    with open(WORKING_PATH) as f:
        return json.load(f)


def save(data: dict) -> None:
    """Persist the entire data structure to the working file (atomic via temp+rename)."""
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        # Stamp metadata
        meta = data.setdefault('metadata', {})
        meta['last_updated'] = datetime.now(timezone.utc).isoformat()
        tmp_path = WORKING_PATH + '.tmp'
        with open(tmp_path, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, WORKING_PATH)


# ===== Targeted update helpers (avoid round-tripping the whole structure) =====

def update_program_metadata(program_id: str, fields: dict) -> dict:
    """Update display_name, subtitle, season, effective_date for a program.
    Returns the updated program dict."""
    data = load()
    if program_id not in data.get('programs', {}):
        raise KeyError(f"Unknown program: {program_id}")
    prog = data['programs'][program_id]
    for key in ('display_name', 'subtitle', 'season', 'effective_date'):
        if key in fields and fields[key] is not None:
            prog[key] = str(fields[key]).strip()
    save(data)
    return prog


def update_event_time(program_id: str, group: str, gender: str, course: str, event_index: int, new_time: str) -> None:
    """Update a single time cell in a program/group/gender/course array."""
    data = load()
    prog = data['programs'][program_id]
    grp = prog['groups'][group]
    if prog.get('multi_level'):
        # USA: times is a 2D array indexed [event_index][level_index]
        # but for multi-level we don't use this single-cell helper; use update_usa_cell
        raise ValueError('Use update_usa_cell for multi-level programs')
    times = grp[gender][course]
    if event_index < 0 or event_index >= len(times):
        raise IndexError('Event index out of range')
    times[event_index] = (new_time or '').strip()
    save(data)


def update_usa_cell(program_id: str, group: str, event_index: int, level_index: int, new_time: str) -> None:
    """Update one cell of the USA Motivational times[event][level] grid."""
    data = load()
    grp = data['programs'][program_id]['groups'][group]
    grp['times'][event_index][level_index] = (new_time or '').strip()
    save(data)


def add_event(program_id: str, group: str, event_name: str, payload: dict) -> int:
    """Append a new event row.

    For non-USA programs, payload is {gender: {course: time, ...}, ...}
    For USA programs, payload is {level_index: time} or {level_name: time}
    Returns the new event_index.
    """
    data = load()
    prog = data['programs'][program_id]
    grp = prog['groups'][group]
    grp['events'].append(event_name.strip())
    new_idx = len(grp['events']) - 1

    if prog.get('multi_level'):
        levels = grp['levels']
        row = []
        for li, level in enumerate(levels):
            v = payload.get(level) or payload.get(str(li)) or ''
            row.append(str(v).strip())
        grp['times'].append(row)
    else:
        for gender_key in prog.get('gender_keys', []):
            for course in ['SCY', 'LCM']:
                arr = grp.get(gender_key, {}).get(course)
                if arr is not None:
                    val = ''
                    if gender_key in payload and isinstance(payload[gender_key], dict):
                        val = payload[gender_key].get(course, '')
                    arr.append(str(val or '').strip())

    save(data)
    return new_idx


def delete_event(program_id: str, group: str, event_index: int) -> None:
    data = load()
    prog = data['programs'][program_id]
    grp = prog['groups'][group]
    if event_index < 0 or event_index >= len(grp['events']):
        raise IndexError('Event index out of range')
    grp['events'].pop(event_index)

    if prog.get('multi_level'):
        grp['times'].pop(event_index)
    else:
        for gender_key in prog.get('gender_keys', []):
            for course in ['SCY', 'LCM']:
                arr = grp.get(gender_key, {}).get(course)
                if arr is not None and event_index < len(arr):
                    arr.pop(event_index)

    save(data)


def rename_event(program_id: str, group: str, event_index: int, new_name: str) -> None:
    data = load()
    grp = data['programs'][program_id]['groups'][group]
    grp['events'][event_index] = (new_name or '').strip()
    save(data)


# ===== Program-level create/delete =====

DEFAULT_USA_LEVELS = ['AAAA', 'AAA', 'AA', 'A', 'BB', 'B']


def create_program(program_id: str, payload: dict) -> dict:
    """Create a new program. Payload keys:
      display_name, subtitle, season, effective_date, multi_level (bool),
      gender_keys (list, for non-multi_level), gender_labels (dict),
      levels (list, for multi_level), groups (list of group names to seed empty)
    """
    data = load()
    program_id = program_id.strip().lower().replace(' ', '_')
    if not program_id or not program_id.replace('_', '').isalnum():
        raise ValueError('Program ID must be alphanumeric (underscores OK)')
    programs = data.setdefault('programs', {})
    if program_id in programs:
        raise ValueError(f'Program "{program_id}" already exists')

    multi_level = bool(payload.get('multi_level'))
    program = {
        'display_name': (payload.get('display_name') or program_id).strip(),
        'subtitle': (payload.get('subtitle') or '').strip(),
        'season': (payload.get('season') or '').strip(),
        'effective_date': (payload.get('effective_date') or '').strip(),
        'groups': {},
    }

    if multi_level:
        program['multi_level'] = True
        levels = payload.get('levels') or DEFAULT_USA_LEVELS
        # Stored as list; not a top-level field but per-group via levels arrays
        # To keep schema aligned with existing USA shape, attach `levels` to each group
        program['_default_levels'] = levels
    else:
        gender_keys = payload.get('gender_keys') or ['girls', 'boys']
        program['gender_keys'] = gender_keys
        program['gender_labels'] = payload.get('gender_labels') or {
            k: ('Girls' if k == 'girls' else 'Boys' if k == 'boys' else
                'Women' if k == 'women' else 'Men' if k == 'men' else k.title())
            for k in gender_keys
        }

    # Seed initial groups
    for grp_name in (payload.get('groups') or []):
        program['groups'][grp_name] = _empty_group(program)

    programs[program_id] = program
    # Strip helper field
    program.pop('_default_levels', None)
    save(data)
    return program


def _empty_group(program: dict) -> dict:
    if program.get('multi_level'):
        levels = program.get('_default_levels') or DEFAULT_USA_LEVELS
        return {'events': [], 'levels': list(levels), 'times': []}
    grp = {'events': []}
    for gk in program.get('gender_keys', []):
        grp[gk] = {'SCY': [], 'LCM': []}
    return grp


def delete_program(program_id: str) -> None:
    data = load()
    if program_id in data.get('programs', {}):
        del data['programs'][program_id]
        save(data)


def add_group(program_id: str, group_name: str) -> dict:
    data = load()
    if program_id not in data.get('programs', {}):
        raise KeyError(f'Unknown program: {program_id}')
    prog = data['programs'][program_id]
    group_name = (group_name or '').strip()
    if not group_name:
        raise ValueError('Group name required')
    if group_name in prog['groups']:
        raise ValueError(f'Group "{group_name}" already exists in this program')
    prog['groups'][group_name] = _empty_group(prog)
    save(data)
    return prog


def delete_group(program_id: str, group_name: str) -> None:
    data = load()
    prog = data.get('programs', {}).get(program_id)
    if prog and group_name in prog.get('groups', {}):
        del prog['groups'][group_name]
        save(data)

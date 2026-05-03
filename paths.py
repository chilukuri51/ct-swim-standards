"""Single source of truth for filesystem locations that need to survive
container restarts on managed hosts (Render, DigitalOcean App Platform, etc.)

Resolution order:
  1. PERSIST_ROOT env var — explicit override
  2. /var/data (Render's standard persistent-disk mount) if it exists + writable
  3. <project>/ — local dev default (mirrors the original layout)

The intent: deploy a persistent disk and mount it at /var/data, and the app
auto-uses it. No env vars required. Locally, behavior is unchanged.
"""

import os


_BASE = os.path.dirname(os.path.abspath(__file__))


def _resolve_persist_root():
    if os.environ.get('PERSIST_ROOT'):
        return os.environ['PERSIST_ROOT']
    # /var/data is Render's standard persistent-disk mount. If it exists,
    # trust it — don't probe with os.access, since group-write perms via
    # setgid can confuse the check depending on supplementary groups.
    if os.path.isdir('/var/data'):
        try:
            os.makedirs('/var/data/data', exist_ok=True)
            return '/var/data'
        except OSError:
            return None
    return None


_PERSIST_ROOT = _resolve_persist_root()

# Where the SQLite file lives. Honors a separate DB_PATH override for cases
# where the user wants the DB in one place and JSON sidecars in another.
DB_PATH = os.environ.get('DB_PATH') or (
    os.path.join(_PERSIST_ROOT, 'swimprogression.db') if _PERSIST_ROOT
    else os.path.join(_BASE, 'swimprogression.db')
)

# Where mutable JSON sidecars live (standards.json working copy,
# team_roster.json one-time seed file).
DATA_DIR = (
    os.path.join(_PERSIST_ROOT, 'data') if _PERSIST_ROOT
    else os.path.join(_BASE, 'data')
)
os.makedirs(DATA_DIR, exist_ok=True)

# The committed baseline standards file is ALWAYS in the repo (read-only,
# regenerated on every deploy). Working copy is on the persistent disk so
# coach edits survive deploys.
PROJECT_DATA_DIR = os.path.join(_BASE, 'data')

# Whether we landed on a persistent disk (useful for status logging).
USING_PERSISTENT_DISK = _PERSIST_ROOT is not None
PERSIST_ROOT = _PERSIST_ROOT

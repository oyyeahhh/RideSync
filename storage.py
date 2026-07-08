"""
Central data directory.

Locally: data files sit next to the code.
On Railway: set DATA_DIR=/data and mount a volume there — data survives deploys.
"""

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

CODE_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(CODE_DIR)))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Locks live alongside data so flock works on the same filesystem.
_LOCK_DIR = DATA_DIR / ".locks"
_LOCK_DIR.mkdir(parents=True, exist_ok=True)

_GROUP_ID_RE = re.compile(r"^grp_[a-zA-Z0-9_-]{1,64}$")


def _validate_group_id(group_id: str) -> None:
    """Defense against path-traversal: reject anything that doesn't match grp_<alnum>."""
    if not isinstance(group_id, str) or not _GROUP_ID_RE.match(group_id):
        raise ValueError(f"invalid group_id: {group_id!r}")


def group_dir(group_id: str) -> Path:
    """Return (and create) the per-group data directory."""
    _validate_group_id(group_id)
    d = DATA_DIR / "groups" / group_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Atomic JSON read/write with file locking ──────────────────────────────────
# Every JSON store in the app does read → modify → write. Without locking + atomic
# replace, concurrent writers silently drop data and a crash mid-write produces a
# truncated file that breaks the next reader. These helpers fix both.

try:
    import fcntl  # POSIX only
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False


class _FileLock:
    """Cross-process advisory lock keyed off a path under DATA_DIR/.locks/."""
    def __init__(self, path: Path):
        # Lock file path mirrors the data file path but lives under .locks/ to
        # avoid polluting data directories. Slashes become __ to flatten.
        key = str(path).replace(str(DATA_DIR), "").lstrip("/").replace("/", "__")
        self.lock_path = _LOCK_DIR / (key + ".lock")
        self._fh = None

    def __enter__(self):
        if not _HAS_FCNTL:
            return self  # No-op on non-POSIX (dev on Windows). Railway is Linux.
        self._fh = open(self.lock_path, "w")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *args):
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None


# ── Per-group data routing (Phase 2c) ─────────────────────────────────────────
# When USE_SUPABASE_DB=1, per-group data files (DATA_DIR/groups/<gid>/<name>.json)
# are stored in Postgres instead of on disk. The three JSON helpers below detect
# such paths and route through db_identity; everything else stays on the
# filesystem. Root-level files (users.json, groups.json, invites.json, …) never
# match this pattern — identity has its own seam in auth.py/groups.py.

def _group_file_key(path: Path):
    """Return (group_id, filename) if `path` is a per-group data file, else None."""
    try:
        rel = Path(path).resolve().relative_to((DATA_DIR / "groups").resolve())
    except (ValueError, OSError):
        return None
    parts = rel.parts
    return (parts[0], parts[1]) if len(parts) == 2 else None


def _pg_group_files_on() -> bool:
    try:
        from db_identity import identity_db_enabled
        return identity_db_enabled()
    except Exception:
        return False


def data_file_exists(path: Path) -> bool:
    """Existence check that understands Postgres-backed group files. Modules
    that gate a read on `path.exists()` must use this instead so they don't
    treat 'lives in Postgres' as 'missing'."""
    key = _group_file_key(path)
    if key and _pg_group_files_on():
        from db_identity import db_group_file_exists
        return db_group_file_exists(*key)
    return Path(path).exists()


def atomic_write_json(path: Path, data) -> None:
    """Write JSON to `path` via tempfile + os.replace so partial writes never
    leave a broken file on disk. Holds an exclusive lock for the duration.
    Per-group files route to Postgres when USE_SUPABASE_DB=1."""
    key = _group_file_key(path)
    if key and _pg_group_files_on():
        from db_identity import db_group_file_save
        db_group_file_save(key[0], key[1], data)
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _FileLock(path):
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def read_json(path: Path, default=None):
    """Read JSON under the same lock. Returns `default` (or []) only if the
    file doesn't exist.

    An existing-but-unreadable file RAISES instead of returning the default.
    Every store in the app does read → modify → write; if a transient read
    failure silently returned [], the next save would persist that empty list
    and wipe the store (the likely cause of the historical users.json wipe).
    Atomic writes mean a corrupt file should never occur — if it does, loud
    failure is the safe behavior.

    Per-group files route to Postgres when USE_SUPABASE_DB=1."""
    key = _group_file_key(path)
    if key and _pg_group_files_on():
        from db_identity import db_group_file_load
        return db_group_file_load(key[0], key[1], default)
    path = Path(path)
    if not path.exists():
        return [] if default is None else default
    with _FileLock(path):
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            raise RuntimeError(
                f"Refusing to read {path.name}: file exists but is unreadable "
                f"({e}). Not returning a default to avoid a wipe on next save."
            ) from e


def update_json(path: Path, mutate_fn, default=None):
    """read → mutate → write under a single lock. `mutate_fn(data)` may mutate
    in place or return a new value; the return value (or the mutated input)
    is what gets persisted. Per-group files route to Postgres when
    USE_SUPABASE_DB=1."""
    key = _group_file_key(path)
    if key and _pg_group_files_on():
        from db_identity import db_group_file_update
        return db_group_file_update(key[0], key[1], mutate_fn, default)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _FileLock(path):
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                # Same rationale as read_json: never treat an unreadable
                # existing file as empty, or this write would wipe it.
                raise RuntimeError(
                    f"Refusing to update {path.name}: file exists but is "
                    f"unreadable ({e})."
                ) from e
        else:
            data = [] if default is None else default
        result = mutate_fn(data)
        if result is None:
            result = data
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(result, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return result


# Legacy flat files that lived directly in DATA_DIR before multi-tenancy.
_LEGACY_FILES = [
    "trip_config.json", "families.json", "rotation.json", "schedule.json",
    "trips.json", "absences.json", "karma.json", "location.json",
    "route_cache.json", "swap_state.json", "confirmations.json",
]


def migrate_legacy_data(legacy_group_id: str = "grp_main") -> bool:
    """
    One-time migration: copy legacy flat data files into the group subdirectory.
    Safe to call repeatedly — only copies if source exists and dest doesn't.
    Returns True if any files were migrated.
    """
    gdir = group_dir(legacy_group_id)
    migrated = False
    for fname in _LEGACY_FILES:
        src = DATA_DIR / fname
        dst = gdir / fname
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            migrated = True
    return migrated

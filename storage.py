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


def atomic_write_json(path: Path, data) -> None:
    """Write JSON to `path` via tempfile + os.replace so partial writes never
    leave a broken file on disk. Holds an exclusive lock for the duration."""
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
    """Read JSON under the same lock. Returns `default` (or []) if the file
    doesn't exist or is mid-write/corrupt."""
    path = Path(path)
    if not path.exists():
        return [] if default is None else default
    with _FileLock(path):
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return [] if default is None else default


def update_json(path: Path, mutate_fn, default=None):
    """read → mutate → write under a single lock. `mutate_fn(data)` may mutate
    in place or return a new value; the return value (or the mutated input)
    is what gets persisted."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _FileLock(path):
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                data = [] if default is None else default
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

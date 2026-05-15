"""
Run once before the app starts on Railway.
Copies seed JSON files from the code directory to DATA_DIR (the volume)
only if they don't already exist there.
"""
import shutil
from storage import DATA_DIR, CODE_DIR

SEED_FILES = [
    "families.json",
    "rotation.json",
    "schedule.json",
    "trip_config.json",
    "users.json",
    "invites.json",
    "trips.json",
    "karma.json",
    "absences.json",
    "geocode_cache.json",
]

if DATA_DIR == CODE_DIR:
    print("DATA_DIR == CODE_DIR, no seeding needed (local mode)")
else:
    for fname in SEED_FILES:
        dest = DATA_DIR / fname
        src = CODE_DIR / fname
        if not dest.exists():
            if src.exists():
                shutil.copy(src, dest)
                print(f"Seeded {fname} → {dest}")
            else:
                print(f"  (no seed for {fname}, skipping)")
        else:
            print(f"  {fname} already exists in DATA_DIR, skipping")
    print("Startup seeding complete.")

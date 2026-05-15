"""
Central data directory.

Locally: data files sit next to the code (no change).
On Railway: set DATA_DIR=/data and mount a volume there — data survives deploys.
"""

import os
from pathlib import Path

CODE_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(CODE_DIR)))
DATA_DIR.mkdir(parents=True, exist_ok=True)

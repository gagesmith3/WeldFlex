"""Make `backend/` importable the same way app.py sees it.

The backend modules import each other flat (`from robot_link import ...`), so the
package directory itself has to be on sys.path rather than the repo root.
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

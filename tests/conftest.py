"""Make `backend/` importable the same way app.py sees it.

The backend modules import each other flat (`from robot_link import ...`), so the
package directory itself has to be on sys.path rather than the repo root.

The repo root goes on too, so tests can drive `tools.stub_robot`. Exercising the
status feed against the same stub the developer runs by hand is deliberate: a
purpose-built test double would let the stub rot into emitting frames the real
decoder no longer accepts.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for path in (str(BACKEND), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

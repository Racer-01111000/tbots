import sys
from pathlib import Path
R = Path(__file__).resolve().parents[1]
for p in (str(R), str(R / "scripts")):
    if p not in sys.path: sys.path.insert(0, p)

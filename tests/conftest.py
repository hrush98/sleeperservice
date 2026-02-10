from pathlib import Path
import os
import sys


ROOT = Path(__file__).resolve().parents[1]
SERVICES_PATH = ROOT / "services"

if str(SERVICES_PATH) not in sys.path:
    sys.path.insert(0, str(SERVICES_PATH))

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("database_url", "sqlite+pysqlite:///:memory:")

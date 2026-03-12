import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("database_url", "sqlite+pysqlite:///:memory:")

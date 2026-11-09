import os
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

url = os.getenv("DATABASE_URL", "sqlite:///./dairyai.db")
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql://", 1)
if url.startswith("postgresql://"):
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)
connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True,
                       **({"pool_size": 3, "max_overflow": 2, "pool_recycle": 1800}
                          if url.startswith("postgresql") else {}))
if url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def ensure_schema_compatibility(database_engine: Engine = engine):
    """Add nullable columns introduced after a database was first created."""
    inspector = inspect(database_engine)
    if "scan_batches" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("scan_batches")}
    if "image_layout" not in columns:
        with database_engine.begin() as connection:
            connection.execute(text("ALTER TABLE scan_batches ADD COLUMN image_layout JSON"))

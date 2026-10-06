import os
from pathlib import Path

from sqlalchemy import create_engine


def get_database_url():
    external_url = os.environ.get("DATABASE_URL")

    if external_url:
        if external_url.startswith("postgresql://"):
            external_url = external_url.replace(
                "postgresql://",
                "postgresql+psycopg://",
                1,
            )

        return external_url

    sqlite_path = Path(__file__).parent / "data" / "tasks.sqlite3"
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{sqlite_path}"


DATABASE_URL = get_database_url()

engine_options = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_options)

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("nombre", String(256), nullable=False),
    Column("apellido", String(256), nullable=False),
    Column("usuario", String(256), nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
)

tokens = Table(
    "tokens",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("expires_at", Integer, nullable=False),
)

tasks = Table(
    "tasks",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("titulo", String(256), nullable=False),
    Column("descripcion", Text, nullable=False, default=""),
    Column("realizada", Boolean, nullable=False, default=False),
)

metadata.create_all(engine)
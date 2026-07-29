from sqlalchemy import Column, DateTime, String, Table, text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


system_metadata = Table(
    "system_metadata",
    Base.metadata,
    Column("key", String(100), primary_key=True),
    Column("value", String(500), nullable=False),
    Column(
        "updated_at",
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    ),
)

from sqlalchemy.sql import func
from sqlalchemy import (
    Column,
    String,
    DateTime,
    UniqueConstraint,
    ForeignKey,
    Boolean,
)
from squad.database import Base, generate_uuid


class BYOKSecret(Base):
    __tablename__ = "byok_secrets"
    secret_id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)
    description = Column(String, nullable=False)
    header_key = Column(String, nullable=False, default="Authorization")
    user_id = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Public here doesn't mean it's exposed/readable, it just means users can create their own instances of it.
    public = Column(Boolean, nullable=False, default=True)

    __table_args__ = (UniqueConstraint("name", name="unique_secrets"),)


class BYOKSecretItem(Base):
    __tablename__ = "byok_secret_items"
    item_id = Column(String, primary_key=True, default=generate_uuid)
    secret_id = Column(
        String, ForeignKey("byok_secrets.secret_id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(String, nullable=False)
    encrypted_value = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "secret_id", name="unique_secret_items"),)

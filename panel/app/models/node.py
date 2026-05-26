import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class NodeStatus(str, enum.Enum):
    provisioning = "provisioning"
    active = "active"
    error = "error"
    disabled = "disabled"


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    country_code: Mapped[str] = mapped_column(String(8))
    label: Mapped[str] = mapped_column(String(64))
    host: Mapped[str] = mapped_column(String(255))
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    status: Mapped[NodeStatus] = mapped_column(
        Enum(NodeStatus, name="node_status"), default=NodeStatus.provisioning
    )

    # server-to-server credential (Trojan password for Panel -> Node tunnel)
    s2s_password: Mapped[str] = mapped_column(String(128))
    s2s_sni: Mapped[str] = mapped_column(String(255))  # SNI used for TLS to node
    s2s_allow_insecure: Mapped[bool] = mapped_column(default=False)

    # Panel XRay config slots
    panel_inbound_tag: Mapped[str] = mapped_column(String(64), unique=True)
    panel_outbound_tag: Mapped[str] = mapped_column(String(64), unique=True)
    panel_inbound_port: Mapped[int] = mapped_column(Integer, unique=True)
    reality_short_id: Mapped[str] = mapped_column(String(32))

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    events: Mapped[list["NodeEvent"]] = relationship(
        back_populates="node", cascade="all, delete-orphan"
    )


class NodeEvent(Base):
    __tablename__ = "node_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"))
    level: Mapped[str] = mapped_column(String(16))  # info | warn | error
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    node: Mapped[Node] = relationship(back_populates="events")

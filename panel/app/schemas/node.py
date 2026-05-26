from datetime import datetime

from pydantic import BaseModel, Field

from app.models.node import NodeStatus


class NodeCreate(BaseModel):
    """Manual node registration (Phase 2): admin already prepared the box."""

    country_code: str = Field(min_length=2, max_length=8)
    label: str = Field(min_length=1, max_length=64)
    host: str = Field(min_length=1, max_length=255)
    ssh_port: int = 22
    # The Trojan s2s credential set up on the exit node when it was prepared.
    s2s_password: str = Field(min_length=8, max_length=128)
    # SNI presented in TLS to the exit node (usually the node's domain).
    s2s_sni: str = Field(min_length=1, max_length=255)
    s2s_allow_insecure: bool = False


class NodeOut(BaseModel):
    id: int
    country_code: str
    label: str
    host: str
    status: NodeStatus
    panel_inbound_port: int
    panel_inbound_tag: str
    panel_outbound_tag: str
    reality_short_id: str
    last_seen_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}

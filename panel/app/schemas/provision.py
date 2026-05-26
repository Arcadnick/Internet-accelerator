from pydantic import BaseModel, Field


class ProvisionNodeRequest(BaseModel):
    country_code: str = Field(min_length=2, max_length=8)
    label: str = Field(min_length=1, max_length=64)
    host: str = Field(min_length=1, max_length=255)
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_password: str | None = None
    ssh_private_key: str | None = None  # PEM-encoded
    admin_ssh_key: str | None = None  # public key to add to root authorized_keys
    # Optional: overrides auto-detection of the panel's public IP.
    panel_ip: str | None = None

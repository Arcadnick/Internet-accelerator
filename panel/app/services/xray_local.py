"""Writes the generated XRay config to disk atomically.

A host-side `xray-watcher.service` (installed by scripts/install_panel.sh)
detects the file change via inotify, validates the new config with
`xray test`, and runs `systemctl restart xray`. This keeps the panel
container unprivileged — no docker.sock, no SSH, no systemd access from
inside the container.
"""

import json
import logging
import os
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Node, Subscription
from app.services.relay_config import build_panel_xray_config

logger = logging.getLogger(__name__)


def _write_atomic(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=path.name, suffix=".tmp", delete=False
    ) as tmp:
        tmp.write(data)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


async def rebuild_and_apply(db: AsyncSession) -> None:
    """Regenerate Panel XRay config from current DB state and write to disk."""
    settings = get_settings()
    nodes = (await db.execute(select(Node))).scalars().all()
    subs = (await db.execute(select(Subscription))).scalars().all()

    config = build_panel_xray_config(nodes, subs, settings)
    payload = json.dumps(config, indent=2, ensure_ascii=False)
    _write_atomic(Path(settings.panel_xray_config_path), payload)
    logger.info(
        "Panel XRay config rewritten (%d nodes, %d subs); watcher will reload",
        len(nodes),
        len(subs),
    )

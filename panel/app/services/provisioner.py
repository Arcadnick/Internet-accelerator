"""SSH-based exit-node provisioner.

Connects to a fresh server with root credentials, uploads bootstrap_node.sh,
runs it with PANEL_IP/NODE_HOST env vars set, streams logs back, and parses
the final stdout line for the s2s credentials. Root password is held only in
memory for the duration of the call — never persisted.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import asyncssh
import httpx

logger = logging.getLogger(__name__)

BOOTSTRAP_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_node.sh"
)


@dataclass(slots=True)
class ProvisionRequest:
    host: str
    ssh_port: int
    ssh_user: str
    ssh_password: str | None
    ssh_private_key: str | None  # PEM
    admin_ssh_key: str | None = None


@dataclass(slots=True)
class ProvisionResult:
    s2s_password: str
    host: str


class ProvisionError(RuntimeError):
    pass


async def detect_panel_ip() -> str:
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get("https://api.ipify.org")
        r.raise_for_status()
        return r.text.strip()


async def _connect(req: ProvisionRequest) -> asyncssh.SSHClientConnection:
    kwargs: dict = {
        "host": req.host,
        "port": req.ssh_port,
        "username": req.ssh_user,
        "known_hosts": None,  # TOFU on bootstrap
        "connect_timeout": 30,
    }
    if req.ssh_private_key:
        kwargs["client_keys"] = [asyncssh.import_private_key(req.ssh_private_key)]
    elif req.ssh_password:
        kwargs["password"] = req.ssh_password
    else:
        raise ProvisionError("Either ssh_password or ssh_private_key required")
    return await asyncssh.connect(**kwargs)


async def provision_node(
    req: ProvisionRequest,
    panel_ip: str | None = None,
) -> AsyncIterator[tuple[str, str | ProvisionResult]]:
    """Yields (event_type, payload) pairs.

    event_type ∈ {"log", "result", "error"}.
      log    -> payload is a string line
      result -> payload is ProvisionResult, terminal success event
      error  -> payload is a string, terminal failure event
    """
    if not BOOTSTRAP_SCRIPT_PATH.exists():
        yield "error", f"bootstrap script missing: {BOOTSTRAP_SCRIPT_PATH}"
        return

    if panel_ip is None:
        try:
            panel_ip = await detect_panel_ip()
            yield "log", f"detected panel IP: {panel_ip}"
        except Exception as e:
            yield "error", f"could not detect panel IP: {e!r}"
            return

    script_body = BOOTSTRAP_SCRIPT_PATH.read_text()
    remote_path = "/tmp/bootstrap_node.sh"

    try:
        yield "log", f"connecting to {req.ssh_user}@{req.host}:{req.ssh_port}"
        async with await _connect(req) as conn:
            yield "log", "ssh connected, uploading bootstrap script"
            async with conn.start_sftp_client() as sftp:
                async with sftp.open(remote_path, "w") as fh:
                    await fh.write(script_body)
                await sftp.chmod(remote_path, 0o700)

            yield "log", "running bootstrap (this takes 1-2 min)..."
            cmd = (
                f"PANEL_IP={panel_ip} "
                f"NODE_HOST={_shell_quote(req.host)} "
                f"ADMIN_SSH_KEY={_shell_quote(req.admin_ssh_key or '')} "
                f"bash {remote_path}"
            )
            process = await conn.create_process(cmd)

            last_stdout_line: str | None = None
            async for tag, line in _merge_streams(process):
                yield "log", f"[{tag}] {line}"
                if tag == "out":
                    last_stdout_line = line

            await process.wait()
            rc = process.exit_status
            if rc != 0:
                yield "error", f"bootstrap exited with code {rc}"
                return

            if last_stdout_line is None:
                yield "error", "bootstrap produced no stdout output"
                return

            try:
                payload = json.loads(last_stdout_line)
            except json.JSONDecodeError:
                yield "error", f"final stdout line is not JSON: {last_stdout_line!r}"
                return

            if payload.get("status") != "ok":
                yield "error", f"bootstrap reported failure: {payload}"
                return

            yield "result", ProvisionResult(
                s2s_password=payload["s2s_password"],
                host=payload["host"],
            )
    except (asyncssh.Error, OSError) as e:
        yield "error", f"ssh failure: {e!r}"


async def _merge_streams(
    process: asyncssh.SSHClientProcess,
) -> AsyncIterator[tuple[str, str]]:
    """Yields (tag, line) from interleaved stdout/stderr; tag ∈ {"out","err"}."""
    queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()

    async def drain(stream, tag: str) -> None:
        try:
            async for line in stream:
                await queue.put((tag, line.rstrip()))
        finally:
            await queue.put(None)

    tasks = [
        asyncio.create_task(drain(process.stdout, "out")),
        asyncio.create_task(drain(process.stderr, "err")),
    ]

    closed = 0
    while closed < 2:
        item = await queue.get()
        if item is None:
            closed += 1
            continue
        yield item

    await asyncio.gather(*tasks, return_exceptions=True)


def _shell_quote(value: str) -> str:
    if not value:
        return "''"
    return "'" + value.replace("'", "'\\''") + "'"

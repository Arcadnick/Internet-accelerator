"""Builds the subscription payload that Happ (and any Xray/v2ray client) consumes.

Output formats:
  * v2ray subscription (base64-encoded newline-separated list of vless:// URIs)
    — universally supported, default.
  * sing-box JSON — richer, supported by Happ natively.

Each entry is one country slot pointing at panel_host:panel_inbound_port with
the user's UUID. The exit node's IP never leaves the panel.
"""

import base64
import json
from collections.abc import Iterable
from urllib.parse import quote

from app.config import Settings
from app.models import Node, Subscription


def _vless_reality_uri(sub: Subscription, node: Node, s: Settings) -> str:
    name = quote(f"{node.country_code} {node.label}")
    params = {
        "type": "tcp",
        "security": "reality",
        "flow": "xtls-rprx-vision",
        "pbk": s.panel_reality_public_key,
        "sni": s.panel_reality_server_name,
        "sid": node.reality_short_id,
        "fp": "chrome",
        "encryption": "none",
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v)
    return (
        f"vless://{sub.xray_uuid}@{s.panel_host}:{node.panel_inbound_port}?{qs}#{name}"
    )


def build_v2ray_subscription(
    sub: Subscription, nodes: Iterable[Node], settings: Settings
) -> str:
    """Returns a base64-encoded blob: newline-separated vless:// URIs."""
    lines = [_vless_reality_uri(sub, n, settings) for n in nodes]
    blob = "\n".join(lines)
    return base64.b64encode(blob.encode()).decode()


def _singbox_outbound(sub: Subscription, node: Node, s: Settings) -> dict:
    return {
        "tag": f"{node.country_code} {node.label}",
        "type": "vless",
        "server": s.panel_host,
        "server_port": node.panel_inbound_port,
        "uuid": sub.xray_uuid,
        "flow": "xtls-rprx-vision",
        "packet_encoding": "xudp",
        "tls": {
            "enabled": True,
            "server_name": s.panel_reality_server_name,
            "utls": {"enabled": True, "fingerprint": "chrome"},
            "reality": {
                "enabled": True,
                "public_key": s.panel_reality_public_key,
                "short_id": node.reality_short_id,
            },
        },
    }


def build_singbox_subscription(
    sub: Subscription, nodes: Iterable[Node], settings: Settings
) -> str:
    nodes_list = list(nodes)
    selectors = [_singbox_outbound(sub, n, settings) for n in nodes_list]
    outbound_tags = [o["tag"] for o in selectors]
    payload = {
        "outbounds": [
            {
                "tag": "select",
                "type": "selector",
                "outbounds": outbound_tags,
                "default": outbound_tags[0] if outbound_tags else None,
            },
            *selectors,
            {"tag": "direct", "type": "direct"},
        ]
    }
    return json.dumps(payload, ensure_ascii=False)

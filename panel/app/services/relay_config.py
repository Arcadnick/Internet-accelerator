"""Generates the XRay-core config for the Panel (relay) host.

The Panel runs XRay in relay mode: one VLESS+Reality inbound per Node (the
country slots clients connect to from Happ) and one Trojan-over-TLS
outbound per Node (the tunnel from Panel -> exit node). Routing maps
inboundTag -> outboundTag so traffic from the German slot leaves through
the German exit, etc.

This is a pure function: (nodes, active subscriptions, settings) -> dict.
"""

from collections.abc import Iterable
from typing import Any

from app.config import Settings
from app.models import Node, Subscription, SubscriptionStatus


API_INBOUND = {
    "tag": "api",
    "listen": "127.0.0.1",
    "port": 10085,
    "protocol": "dokodemo-door",
    "settings": {"address": "127.0.0.1"},
}


def _client_entry(sub: Subscription) -> dict[str, Any]:
    return {
        "id": sub.xray_uuid,
        "flow": "xtls-rprx-vision",
        "email": sub.xray_email,
        "level": 0,
    }


def _inbound_for_node(node: Node, clients: list[dict[str, Any]], s: Settings) -> dict[str, Any]:
    return {
        "tag": node.panel_inbound_tag,
        "listen": "0.0.0.0",
        "port": node.panel_inbound_port,
        "protocol": "vless",
        "settings": {
            "clients": clients,
            "decryption": "none",
        },
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "show": False,
                "dest": s.panel_reality_dest,
                "xver": 0,
                "serverNames": [s.panel_reality_server_name],
                "privateKey": s.panel_reality_private_key,
                "shortIds": [node.reality_short_id],
            },
        },
        "sniffing": {
            "enabled": True,
            "destOverride": ["http", "tls", "quic"],
        },
    }


def _outbound_for_node(node: Node) -> dict[str, Any]:
    return {
        "tag": node.panel_outbound_tag,
        "protocol": "trojan",
        "settings": {
            "servers": [
                {
                    "address": node.host,
                    "port": 443,
                    "password": node.s2s_password,
                }
            ]
        },
        "streamSettings": {
            "network": "tcp",
            "security": "tls",
            "tlsSettings": {
                "serverName": node.s2s_sni,
                "allowInsecure": node.s2s_allow_insecure,
            },
        },
    }


def build_panel_xray_config(
    nodes: Iterable[Node],
    subscriptions: Iterable[Subscription],
    settings: Settings,
) -> dict[str, Any]:
    nodes_list = list(nodes)
    active_subs = [s for s in subscriptions if s.status == SubscriptionStatus.active]
    clients = [_client_entry(s) for s in active_subs]

    inbounds: list[dict[str, Any]] = [API_INBOUND]
    outbounds: list[dict[str, Any]] = [
        {"protocol": "freedom", "tag": "direct"},
        {"protocol": "blackhole", "tag": "block"},
    ]
    routing_rules: list[dict[str, Any]] = [
        {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
    ]

    for node in nodes_list:
        inbounds.append(_inbound_for_node(node, clients, settings))
        outbounds.append(_outbound_for_node(node))
        routing_rules.append(
            {
                "type": "field",
                "inboundTag": [node.panel_inbound_tag],
                "outboundTag": node.panel_outbound_tag,
            }
        )

    return {
        "log": {"loglevel": "warning"},
        "api": {"tag": "api", "services": ["HandlerService", "StatsService"]},
        "stats": {},
        "policy": {
            "levels": {
                "0": {"statsUserUplink": True, "statsUserDownlink": True}
            },
            "system": {
                "statsInboundUplink": True,
                "statsInboundDownlink": True,
            },
        },
        "inbounds": inbounds,
        "outbounds": outbounds,
        "routing": {"rules": routing_rules},
    }

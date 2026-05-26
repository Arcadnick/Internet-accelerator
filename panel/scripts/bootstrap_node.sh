#!/usr/bin/env bash
# Bootstraps an exit node: installs XRay-core, configures it as a Trojan-only
# inbound on :443 that accepts ONLY traffic from the panel's IP, and prints
# the s2s credentials back to the panel via stdout (final line is JSON).
#
# Required env vars passed by the provisioner:
#   PANEL_IP        - source IP allowed in ufw (the main server)
#   NODE_HOST       - public hostname or IP of this node (used as TLS SNI)
#   ADMIN_SSH_KEY   - (optional) public key added to /root/.ssh/authorized_keys
#
# Output (last line of stdout): {"s2s_password":"...","host":"...","status":"ok"}

set -euo pipefail

: "${PANEL_IP:?PANEL_IP env var required}"
: "${NODE_HOST:?NODE_HOST env var required}"
ADMIN_SSH_KEY="${ADMIN_SSH_KEY:-}"

log() { echo "[bootstrap] $*" >&2; }

if [[ $EUID -ne 0 ]]; then
    log "Run as root."
    exit 1
fi

log "Installing prerequisites"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y >&2
apt-get install -y curl unzip jq ufw openssl ca-certificates >&2

log "Downloading XRay-core"
XRAY_VERSION=$(curl -fsSL https://api.github.com/repos/XTLS/Xray-core/releases/latest \
    | jq -r .tag_name)
ARCH=$(uname -m)
case "$ARCH" in
    x86_64)  ASSET="Xray-linux-64.zip" ;;
    aarch64) ASSET="Xray-linux-arm64-v8a.zip" ;;
    *) log "Unsupported arch: $ARCH"; exit 1 ;;
esac

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
curl -fsSL -o "$TMP/xray.zip" \
    "https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/${ASSET}" >&2
unzip -q "$TMP/xray.zip" -d "$TMP/xray"
install -m 0755 "$TMP/xray/xray" /usr/local/bin/xray

mkdir -p /usr/local/etc/xray /etc/xray-tls

log "Generating self-signed TLS cert for node"
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout /etc/xray-tls/key.pem \
    -out    /etc/xray-tls/cert.pem \
    -subj "/CN=${NODE_HOST}" >&2 2>&1
chmod 600 /etc/xray-tls/key.pem

S2S_PASSWORD=$(openssl rand -hex 32)

log "Writing XRay config (Trojan inbound on :443)"
cat >/usr/local/etc/xray/config.json <<EOF
{
  "log": {"loglevel": "warning"},
  "inbounds": [{
    "tag": "exit-in",
    "listen": "0.0.0.0",
    "port": 443,
    "protocol": "trojan",
    "settings": {
      "clients": [{"password": "${S2S_PASSWORD}"}]
    },
    "streamSettings": {
      "network": "tcp",
      "security": "tls",
      "tlsSettings": {
        "certificates": [{
          "certificateFile": "/etc/xray-tls/cert.pem",
          "keyFile": "/etc/xray-tls/key.pem"
        }]
      }
    }
  }],
  "outbounds": [
    {"protocol": "freedom", "tag": "direct"},
    {"protocol": "blackhole", "tag": "block"}
  ]
}
EOF

log "Installing systemd unit"
cat >/etc/systemd/system/xray.service <<'EOF'
[Unit]
Description=XRay-core (exit node)
After=network.target nss-lookup.target

[Service]
User=root
ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json
Restart=on-failure
RestartSec=3
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now xray >&2
systemctl restart xray >&2

log "Configuring firewall: 443 only from PANEL_IP=$PANEL_IP"
ufw --force reset >&2
ufw default deny incoming >&2
ufw default allow outgoing >&2
ufw allow OpenSSH >&2
ufw allow from "$PANEL_IP" to any port 443 proto tcp >&2
ufw --force enable >&2

if [[ -n "$ADMIN_SSH_KEY" ]]; then
    log "Adding admin SSH key to /root/.ssh/authorized_keys"
    mkdir -p /root/.ssh && chmod 700 /root/.ssh
    if ! grep -qF "$ADMIN_SSH_KEY" /root/.ssh/authorized_keys 2>/dev/null; then
        echo "$ADMIN_SSH_KEY" >> /root/.ssh/authorized_keys
        chmod 600 /root/.ssh/authorized_keys
    fi
fi

log "Done. Emitting result JSON."
# Final stdout line: must be a single line JSON object.
printf '{"status":"ok","s2s_password":"%s","host":"%s"}\n' "$S2S_PASSWORD" "$NODE_HOST"

#!/usr/bin/env bash
# Installs XRay-core on the main (panel) server as a systemd unit.
# Run as root on a fresh Ubuntu 22.04/24.04 or Debian 12 box.
#
# After install:
#   - xray binary at /usr/local/bin/xray
#   - config dir   at /usr/local/etc/xray/
#   - systemd unit `xray.service` enabled and started
#   - gRPC API listening on 127.0.0.1:10085 (localhost only)
#   - Reality keypair printed to stdout; copy into panel's .env

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi

XRAY_VERSION="${XRAY_VERSION:-latest}"
XRAY_DIR="/usr/local/etc/xray"
XRAY_BIN="/usr/local/bin/xray"

echo "==> Installing prerequisites"
apt-get update -y
apt-get install -y curl unzip jq ca-certificates inotify-tools

echo "==> Fetching XRay-core release info"
if [[ "$XRAY_VERSION" == "latest" ]]; then
    XRAY_VERSION=$(curl -fsSL https://api.github.com/repos/XTLS/Xray-core/releases/latest \
        | jq -r .tag_name)
fi
echo "    version: $XRAY_VERSION"

ARCH=$(uname -m)
case "$ARCH" in
    x86_64)  XRAY_ASSET="Xray-linux-64.zip" ;;
    aarch64) XRAY_ASSET="Xray-linux-arm64-v8a.zip" ;;
    *)       echo "Unsupported arch: $ARCH" >&2 ; exit 1 ;;
esac

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "==> Downloading $XRAY_ASSET"
curl -fsSL -o "$TMP/xray.zip" \
    "https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/${XRAY_ASSET}"
unzip -q "$TMP/xray.zip" -d "$TMP/xray"
install -m 0755 "$TMP/xray/xray" "$XRAY_BIN"

mkdir -p "$XRAY_DIR"

echo "==> Generating Reality keypair"
KEYPAIR=$("$XRAY_BIN" x25519)
PRIV=$(echo "$KEYPAIR" | awk -F': ' '/Private/ {print $2}')
PUB=$(echo "$KEYPAIR"  | awk -F': ' '/Public/  {print $2}')

if [[ ! -f "$XRAY_DIR/config.json" ]]; then
    cat >"$XRAY_DIR/config.json" <<'EOF'
{
  "log": {"loglevel": "warning"},
  "api": {"tag": "api", "services": ["HandlerService", "StatsService"]},
  "stats": {},
  "policy": {
    "levels": {"0": {"statsUserUplink": true, "statsUserDownlink": true}},
    "system": {"statsInboundUplink": true, "statsInboundDownlink": true}
  },
  "inbounds": [
    {
      "tag": "api",
      "listen": "127.0.0.1",
      "port": 10085,
      "protocol": "dokodemo-door",
      "settings": {"address": "127.0.0.1"}
    }
  ],
  "outbounds": [
    {"protocol": "freedom", "tag": "direct"}
  ],
  "routing": {
    "rules": [
      {"type": "field", "inboundTag": ["api"], "outboundTag": "api"}
    ]
  }
}
EOF
fi

echo "==> Installing systemd unit"
cat >/etc/systemd/system/xray.service <<EOF
[Unit]
Description=XRay-core (panel relay)
After=network.target nss-lookup.target

[Service]
User=root
ExecStart=$XRAY_BIN run -config $XRAY_DIR/config.json
Restart=on-failure
RestartSec=3
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

echo "==> Installing xray-watcher (auto-reload on config change)"
cat >/usr/local/bin/xray-watcher.sh <<'WATCH'
#!/usr/bin/env bash
# Restarts xray.service whenever /usr/local/etc/xray/config.json is rewritten.
# Debounces rapid successive writes (panel may regenerate the file multiple
# times in a single transaction).
set -eu
CFG="/usr/local/etc/xray/config.json"
while inotifywait -qq -e close_write,moved_to "$(dirname "$CFG")"; do
    sleep 1   # debounce burst writes
    if [[ -s "$CFG" ]] && /usr/local/bin/xray test -config "$CFG" >/dev/null 2>&1; then
        systemctl restart xray
        logger -t xray-watcher "config changed, xray restarted"
    else
        logger -t xray-watcher "config invalid or empty; skipping restart"
    fi
done
WATCH
chmod +x /usr/local/bin/xray-watcher.sh

cat >/etc/systemd/system/xray-watcher.service <<EOF
[Unit]
Description=XRay config watcher (auto-reload)
After=network.target xray.service

[Service]
Type=simple
ExecStart=/usr/local/bin/xray-watcher.sh
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now xray
systemctl enable --now xray-watcher
systemctl restart xray

cat <<EOF

==> XRay installed and running.
==> Add the following to panel's .env file:

PANEL_REALITY_PRIVATE_KEY=$PRIV
PANEL_REALITY_PUBLIC_KEY=$PUB
PANEL_XRAY_API_ADDR=127.0.0.1:10085
PANEL_XRAY_CONFIG_PATH=$XRAY_DIR/config.json

Then restart the panel container:  docker compose restart panel-api arq-worker
EOF

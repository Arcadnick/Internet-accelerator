#!/usr/bin/env bash
# One-shot deployer for the VPN panel.
#
# Usage (on a fresh Ubuntu 22.04/24.04 root shell):
#   bash <(curl -fsSL https://raw.githubusercontent.com/Arcadnick/Internet-accelerator/main/deploy.sh)
#
# Idempotent: safe to re-run. Secrets are kept across runs in /root/.panel-secrets.env.

set -euo pipefail

# ---------------------------------------------------------------------------
# Settings (override via env vars before running)
# ---------------------------------------------------------------------------
PANEL_HOST="${PANEL_HOST:-accelerator.rubineagle.ru}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@${PANEL_HOST}}"
REPO_URL="${REPO_URL:-https://github.com/Arcadnick/Internet-accelerator.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/accelerator}"
PANEL_DIR="$INSTALL_DIR/panel"
SECRET_FILE="${SECRET_FILE:-/root/.panel-secrets.env}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log() { printf "\n\033[1;36m▸ %s\033[0m\n" "$*"; }
ok()  { printf "  \033[1;32m✓\033[0m %s\n" "$*"; }
warn(){ printf "  \033[1;33m!\033[0m %s\n" "$*"; }

if [ "$EUID" -ne 0 ]; then
    echo "Run as root." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. Disable preinstalled brute-force protection that interferes with deploys
# ---------------------------------------------------------------------------
log "Disabling preinstalled SSH bouncers (fail2ban / sshguard / crowdsec)"
for svc in fail2ban sshguard crowdsec crowdsec-firewall-bouncer-iptables; do
    if systemctl list-unit-files --type=service 2>/dev/null | grep -q "^${svc}\.service"; then
        systemctl stop "$svc" 2>/dev/null || true
        systemctl disable "$svc" 2>/dev/null || true
        ok "disabled $svc"
    fi
done
# Wipe any leftover banlist sets
iptables -F f2b-sshd 2>/dev/null || true
nft list table inet sshguard 2>/dev/null && nft flush table inet sshguard || true
nft list table inet crowdsec 2>/dev/null && nft flush table inet crowdsec || true

# ---------------------------------------------------------------------------
# 2. Install OS packages
# ---------------------------------------------------------------------------
log "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    ca-certificates curl gnupg ufw git inotify-tools openssl unzip jq

# ---------------------------------------------------------------------------
# 3. Docker + Compose plugin + registry mirror
# ---------------------------------------------------------------------------
log "Installing Docker"
if ! command -v docker >/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
    ok "docker installed"
else
    ok "docker already installed: $(docker --version)"
fi

log "Configuring Docker registry mirror (mirror.gcr.io) to avoid Hub rate limits"
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<EOF
{
  "registry-mirrors": ["https://mirror.gcr.io"],
  "log-driver": "json-file",
  "log-opts": {"max-size": "10m", "max-file": "3"}
}
EOF
systemctl enable --now docker >/dev/null
systemctl restart docker

# ---------------------------------------------------------------------------
# 4. UFW (firewall) — explicit allowlist
# ---------------------------------------------------------------------------
log "Configuring ufw (22, 80, 443, 10001-10999)"
ufw allow 22/tcp           >/dev/null
ufw allow 80/tcp           >/dev/null
ufw allow 443/tcp          >/dev/null
ufw allow 10001:10999/tcp  >/dev/null
ufw --force enable          >/dev/null
ok "ufw active"

# ---------------------------------------------------------------------------
# 5. Clone / update the repo
# ---------------------------------------------------------------------------
log "Fetching code from $REPO_URL (branch $REPO_BRANCH)"
if [ -d "$INSTALL_DIR/.git" ]; then
    git -C "$INSTALL_DIR" fetch --quiet origin "$REPO_BRANCH"
    git -C "$INSTALL_DIR" reset --hard "origin/$REPO_BRANCH"
    ok "repo updated"
else
    rm -rf "$INSTALL_DIR"
    git clone --quiet --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
    ok "repo cloned"
fi

# ---------------------------------------------------------------------------
# 6. Install Panel XRay (binary + systemd + watcher) — idempotent
# ---------------------------------------------------------------------------
log "Installing Panel XRay (host-side)"
if [ ! -x /usr/local/bin/xray ]; then
    # Run the install script; it writes binary + systemd unit + watcher.
    bash "$PANEL_DIR/scripts/install_panel.sh" > /tmp/install_panel.log 2>&1
    ok "xray installed"
else
    ok "xray already present: $(/usr/local/bin/xray version | head -1)"
fi
# Ensure systemd unit + watcher even if xray binary was present already
if ! systemctl is-enabled xray >/dev/null 2>&1 || ! systemctl is-enabled xray-watcher >/dev/null 2>&1; then
    # Re-run installer to (re)create systemd units; it's safe because the
    # config block uses [[ ! -f ]] guard.
    bash "$PANEL_DIR/scripts/install_panel.sh" >> /tmp/install_panel.log 2>&1 || true
fi

# ---------------------------------------------------------------------------
# 7. Secrets — generate ONCE, persist across re-runs
# ---------------------------------------------------------------------------
log "Preparing secrets ($SECRET_FILE)"
if [ ! -f "$SECRET_FILE" ]; then
    umask 077
    {
        echo "JWT_SECRET=$(openssl rand -hex 32)"
        echo "ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=' | head -c 20)"
        echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"
    } > "$SECRET_FILE"
    ok "new secrets generated"
else
    ok "reusing existing secrets"
fi

# Pull / generate Reality keypair. Prefer existing one from xray config so we
# don't break already-issued subscriptions.
if ! grep -q '^PANEL_REALITY_PRIVATE_KEY=' "$SECRET_FILE"; then
    # Try to extract from existing xray config first.
    PRIV=$(jq -r '.. | objects | .privateKey? // empty' \
        /usr/local/etc/xray/config.json 2>/dev/null | head -1)
    if [ -z "$PRIV" ]; then
        # Generate fresh.
        eval "$(/usr/local/bin/xray x25519 | awk -F': ' '
            /Private/ {print "PRIV=" $2}
            /Public/  {print "PUB="  $2}')"
        echo "PANEL_REALITY_PRIVATE_KEY=$PRIV" >> "$SECRET_FILE"
        echo "PANEL_REALITY_PUBLIC_KEY=$PUB"   >> "$SECRET_FILE"
        ok "reality keypair generated"
    else
        # Need to derive public key from private. xray x25519 -i <priv> does this.
        PUB=$(/usr/local/bin/xray x25519 -i "$PRIV" | awk -F': ' '/Public/ {print $2}')
        echo "PANEL_REALITY_PRIVATE_KEY=$PRIV" >> "$SECRET_FILE"
        echo "PANEL_REALITY_PUBLIC_KEY=$PUB"   >> "$SECRET_FILE"
        ok "reality keypair recovered from xray config"
    fi
fi

# shellcheck source=/dev/null
source "$SECRET_FILE"

# ---------------------------------------------------------------------------
# 8. Write panel .env
# ---------------------------------------------------------------------------
log "Writing $PANEL_DIR/.env"
cat > "$PANEL_DIR/.env" <<EOF
PANEL_ENV=production

POSTGRES_USER=panel
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_DB=panel

JWT_SECRET=$JWT_SECRET
ADMIN_EMAIL=$ADMIN_EMAIL
ADMIN_PASSWORD=$ADMIN_PASSWORD

PANEL_HOST=$PANEL_HOST
PANEL_XRAY_API_ADDR=host.docker.internal:10085

PANEL_REALITY_PRIVATE_KEY=$PANEL_REALITY_PRIVATE_KEY
PANEL_REALITY_PUBLIC_KEY=$PANEL_REALITY_PUBLIC_KEY
EOF
chmod 600 "$PANEL_DIR/.env"
ok ".env written"

# ---------------------------------------------------------------------------
# 9. Bring up the stack
# ---------------------------------------------------------------------------
log "Building and starting docker compose stack"
cd "$PANEL_DIR"
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull \
    --ignore-buildable 2>&1 | tail -10 || true
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

# ---------------------------------------------------------------------------
# 10. Wait for panel-api healthz
# ---------------------------------------------------------------------------
log "Waiting for panel-api to report healthy (up to 120s)"
HEALTHY=0
for i in $(seq 1 60); do
    sleep 2
    if docker compose exec -T panel-api curl -sf http://localhost:8000/healthz 2>/dev/null | grep -q ok; then
        HEALTHY=1
        ok "healthy after $((i*2))s"
        break
    fi
done

if [ "$HEALTHY" -ne 1 ]; then
    warn "panel-api not healthy yet; printing recent logs:"
    docker compose logs --tail=40 panel-api
fi

# ---------------------------------------------------------------------------
# 11. Final report
# ---------------------------------------------------------------------------
echo
echo "============================================================"
echo "  DEPLOY COMPLETE"
echo "============================================================"
docker compose ps
echo
echo "  Admin URL:       https://$PANEL_HOST/admin"
echo "  Admin email:     $ADMIN_EMAIL"
echo "  Admin password:  $ADMIN_PASSWORD"
echo
echo "  Secrets:         $SECRET_FILE  (back this up)"
echo "  Repo:            $INSTALL_DIR"
echo "  XRay config:     /usr/local/etc/xray/config.json"
echo
echo "  Caddy will request Let's Encrypt cert on first browser visit."
echo "  If something looks off:"
echo "    cd $PANEL_DIR && docker compose logs -f caddy panel-api"
echo "============================================================"

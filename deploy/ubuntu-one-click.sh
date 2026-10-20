#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
note() { printf '\n==> %s\n' "$*"; }

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "Run with sudo: sudo bash deploy/ubuntu-one-click.sh"
[[ -r /etc/os-release ]] || die "Cannot identify this operating system"
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || die "This installer supports Ubuntu only"

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
ENV_FILE="$SCRIPT_DIR/.env"
COMPOSE_FILE="$SCRIPT_DIR/compose.yaml"
if [[ -e "$ENV_FILE" && "${FORCE_RECONFIGURE:-0}" != "1" ]]; then
  die "$ENV_FILE already exists. Refusing to replace deployment secrets. Set FORCE_RECONFIGURE=1 only for a deliberate fresh deployment."
fi

ask() {
  local variable=$1 prompt=$2 default=${3:-} value
  value=${!variable:-}
  if [[ -z "$value" ]]; then
    read -r -p "$prompt${default:+ [$default]}: " value
    value=${value:-$default}
  fi
  printf -v "$variable" '%s' "$value"
}

ask_secret() {
  local variable=$1 prompt=$2 value
  value=${!variable:-}
  if [[ -z "$value" ]]; then
    read -r -s -p "$prompt (leave blank if not needed): " value
    printf '\n'
  fi
  printf -v "$variable" '%s' "$value"
}

dotenv_value() {
  local value=$1
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || die "Environment values cannot contain newlines"
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//\$/\\\$}
  printf '"%s"' "$value"
}

ask DEPLOY_DOMAIN "Public domain (DNS must already point to this server)"
DEPLOY_DOMAIN=${DEPLOY_DOMAIN#http://}; DEPLOY_DOMAIN=${DEPLOY_DOMAIN#https://}; DEPLOY_DOMAIN=${DEPLOY_DOMAIN%%/*}
[[ "$DEPLOY_DOMAIN" =~ ^[A-Za-z0-9.-]+$ && "$DEPLOY_DOMAIN" == *.* ]] || die "Invalid domain"
ask LETSENCRYPT_EMAIL "Email for Let's Encrypt notices"
[[ "$LETSENCRYPT_EMAIL" == *@*.* ]] || die "Invalid email"
ask ADMIN_EMAIL "Initial superadmin email" "superadmin"
ask ALLOW_REGISTRATION "Allow public user registration? (true/false)" "false"
ask LLM_PROVIDER "LLM provider" "openai_compatible"
ask DEEP_MODEL "Deep-thinking model"
[[ -n "$DEEP_MODEL" ]] || die "A deep-thinking model is required"
ask QUICK_MODEL "Quick-thinking model" "$DEEP_MODEL"
ask LLM_BACKEND_URL "LLM backend URL (blank for provider default)"
ask_secret LLM_API_KEY "LLM API key"
case "$LLM_PROVIDER" in
  openai) LLM_KEY_NAME=OPENAI_API_KEY ;;
  google) LLM_KEY_NAME=GOOGLE_API_KEY ;;
  anthropic) LLM_KEY_NAME=ANTHROPIC_API_KEY ;;
  xai) LLM_KEY_NAME=XAI_API_KEY ;;
  deepseek) LLM_KEY_NAME=DEEPSEEK_API_KEY ;;
  openrouter) LLM_KEY_NAME=OPENROUTER_API_KEY ;;
  *) LLM_KEY_NAME=OPENAI_COMPATIBLE_API_KEY ;;
esac

openai_key=""
google_key=""
anthropic_key=""
xai_key=""
deepseek_key=""
openrouter_key=""
openai_compat_key=""
case "$LLM_KEY_NAME" in
  OPENAI_API_KEY) openai_key=$LLM_API_KEY ;;
  GOOGLE_API_KEY) google_key=$LLM_API_KEY ;;
  ANTHROPIC_API_KEY) anthropic_key=$LLM_API_KEY ;;
  XAI_API_KEY) xai_key=$LLM_API_KEY ;;
  DEEPSEEK_API_KEY) deepseek_key=$LLM_API_KEY ;;
  OPENROUTER_API_KEY) openrouter_key=$LLM_API_KEY ;;
  *) openai_compat_key=$LLM_API_KEY ;;
esac

note "Installing Docker, Nginx, Certbot, and OpenSSL"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl openssl nginx certbot python3-certbot-nginx docker.io
if ! docker compose version >/dev/null 2>&1; then
  apt-get install -y docker-compose-v2 2>/dev/null || apt-get install -y docker-compose-plugin 2>/dev/null || true
  if ! docker compose version >/dev/null 2>&1; then
    note "Installing Docker Compose CLI plugin directly"
    mkdir -p /usr/local/lib/docker/cli-plugins
    ARCH=$(uname -m)
    case "$ARCH" in
      x86_64) COMPOSE_ARCH="x86_64" ;;
      aarch64|arm64) COMPOSE_ARCH="aarch64" ;;
      *) COMPOSE_ARCH="$ARCH" ;;
    esac
    curl -sSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${COMPOSE_ARCH}" -o /usr/local/lib/docker/cli-plugins/docker-compose || true
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose 2>/dev/null || true
  fi
fi
if ! docker compose version >/dev/null 2>&1; then
  die "Docker Compose could not be found or installed. Please install docker-compose-plugin."
fi
systemctl enable --now docker nginx

POSTGRES_PASSWORD=$(openssl rand -hex 32)
ADMIN_PASSWORD=$(openssl rand -hex 18)
FERNET_KEY=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')
git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true
APP_VERSION=$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || printf local)

note "Writing protected deployment configuration"
cat > "$ENV_FILE" <<EOF
APP_VERSION=$(dotenv_value "$APP_VERSION")
APP_PORT=8000
POSTGRES_PASSWORD=$(dotenv_value "$POSTGRES_PASSWORD")
TA_BOOTSTRAP_ADMIN_EMAIL=$(dotenv_value "$ADMIN_EMAIL")
TA_BOOTSTRAP_ADMIN_PASSWORD=$(dotenv_value "$ADMIN_PASSWORD")
TA_ALLOW_REGISTRATION=$(dotenv_value "$ALLOW_REGISTRATION")
TA_JOB_TIMEOUT=1800
TA_PAYMENT_PROVIDER=
TA_PUBLIC_ORIGIN=$(dotenv_value "https://$DEPLOY_DOMAIN")
SSI_MCP_REDIRECT_URI=$(dotenv_value "https://$DEPLOY_DOMAIN/api/integrations/ssi/callback")
SSI_MCP_TOKEN_ENCRYPTION_KEY=$(dotenv_value "$FERNET_KEY")
SSI_MCP_CLIENT_ID=
SSI_MCP_CLIENT_SECRET=
SSI_MCP_ACCESS_TOKEN=
VN_MARKET_HOLIDAYS=
TRADINGAGENTS_LLM_PROVIDER=$(dotenv_value "$LLM_PROVIDER")
TRADINGAGENTS_DEEP_THINK_LLM=$(dotenv_value "$DEEP_MODEL")
TRADINGAGENTS_QUICK_THINK_LLM=$(dotenv_value "$QUICK_MODEL")
TRADINGAGENTS_LLM_BACKEND_URL=$(dotenv_value "$LLM_BACKEND_URL")
OPENAI_API_KEY=$(dotenv_value "$openai_key")
GOOGLE_API_KEY=$(dotenv_value "$google_key")
ANTHROPIC_API_KEY=$(dotenv_value "$anthropic_key")
XAI_API_KEY=$(dotenv_value "$xai_key")
DEEPSEEK_API_KEY=$(dotenv_value "$deepseek_key")
OPENROUTER_API_KEY=$(dotenv_value "$openrouter_key")
OPENAI_COMPATIBLE_API_KEY=$(dotenv_value "$openai_compat_key")
TRADINGAGENTS_LLM_TIMEOUT=60
TRADINGAGENTS_LLM_MAX_RETRIES=4
EOF
chmod 600 "$ENV_FILE"

note "Configuring Nginx"
cat > "/etc/nginx/sites-available/tradingagents-vn" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DEPLOY_DOMAIN;
    client_max_body_size 64k;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
EOF
chmod 644 /etc/nginx/sites-available/tradingagents-vn
ln -sfn /etc/nginx/sites-available/tradingagents-vn /etc/nginx/sites-enabled/tradingagents-vn
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

note "Building and starting TradingAgentsVN"
cd "$REPO_DIR"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up --build -d

note "Waiting for the API health check"
healthy=false
for _ in $(seq 1 60); do
  if curl --fail --silent http://127.0.0.1:8000/api/health >/dev/null; then healthy=true; break; fi
  sleep 3
done
[[ "$healthy" == true ]] || {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" logs --tail=150 dashboard
  die "Dashboard did not become healthy"
}

note "Requesting the HTTPS certificate"
SSL_SUCCESS=false
if certbot --nginx --non-interactive --agree-tos --redirect \
  --email "$LETSENCRYPT_EMAIL" -d "$DEPLOY_DOMAIN"; then
  SSL_SUCCESS=true
else
  printf '\nWARNING: Certbot SSL registration failed!\n' >&2
  printf 'Possible causes: DNS has not propagated, or port 80/443 is blocked by a firewall.\n' >&2
  printf 'You can run "certbot --nginx -d %s" manually once DNS/ports are resolved.\n\n' "$DEPLOY_DOMAIN" >&2
fi

# Bootstrap is idempotent. Remove the plaintext bootstrap password after the
# first healthy startup and recreate application containers without it.
sed -i 's/^TA_BOOTSTRAP_ADMIN_PASSWORD=.*/TA_BOOTSTRAP_ADMIN_PASSWORD=/' "$ENV_FILE"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --force-recreate dashboard worker

note "Deployment complete"
if [[ "$SSL_SUCCESS" == "true" ]]; then
  printf 'URL: https://%s/login\n' "$DEPLOY_DOMAIN"
else
  printf 'URL: http://%s/login (HTTPS setup pending: run certbot manually once DNS is ready)\n' "$DEPLOY_DOMAIN"
fi
printf 'Superadmin: %s\n' "$ADMIN_EMAIL"
printf 'Temporary password (shown once): %s\n' "$ADMIN_PASSWORD"
printf 'Change the password immediately after signing in.\n'
printf 'Then open Account -> Sign in with SSI account to enable the server-wide SSI source.\n'
printf 'Secrets file: %s (mode 600; never commit it)\n' "$ENV_FILE"

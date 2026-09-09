#!/usr/bin/env bash
# Augura 一键安装（macOS / Linux）——Multica/Ollama 式 curl 直装：
#
#   curl -fsSL https://raw.githubusercontent.com/augura-os/augura/main/scripts/install.sh | bash
#
# 不需要 clone 仓库：脚本只下载 docker-compose.yml + .env.example 到
# ~/augura（可用 AUGURA_HOME 覆盖），其余全部本地完成。幂等，可重复运行
# （重复运行 = 更新镜像并重启）。
#
# 兼容性：macOS 自带 bash 3.2 / Linux bash。注意 bash 3.2 会把变量名后的
# 多字节字符（全角括号等）吞进变量名——$VAR 后紧跟中文标点必须写 ${VAR}，
# CI 有 lint 强制检查。
#
# 国内网络：ghcr.io 不通时自动切 AUGURA_IMAGE_MIRROR（默认 ghcr.nju.edu.cn）；
# raw.githubusercontent.com 失败自动回退 jsdelivr。
#
# 流程：检测 Docker →（缺失则引导安装）→ 启动引擎 → 生成 .env（随机密码）
#       → 拉取预构建镜像 → 健康检查 → 打开浏览器。
set -u

REPO_RAW="${AUGURA_REPO_RAW:-https://raw.githubusercontent.com/augura-os/augura/main}"
# 冒烟测试/自定义部署可覆盖文件来源（默认从 GitHub raw 拉取）
COMPOSE_URL="${AUGURA_COMPOSE_URL:-$REPO_RAW/docker-compose.yml}"
ENV_EXAMPLE_URL="${AUGURA_ENV_URL:-$REPO_RAW/.env.example}"
HOME_DIR="${AUGURA_HOME:-$HOME/augura}"

info() { printf '[*] %s\n' "$1"; }
ok()   { printf '[OK] %s\n' "$1"; }
warn() { printf '[!] %s\n' "$1"; }
fatal() {
    printf '[x] %s\n' "$1" >&2
    exit 1
}

OS="$(uname -s)"
case "$OS" in
    Darwin|Linux) ;;
    *) fatal "暂不支持 ${OS}（Windows 请双击仓库里的 install.bat）" ;;
esac

echo "============================================================"
echo "  Augura 一键安装向导（${OS}）"
echo "  数据存在你自己的机器上，不上传任何素材与投放明细"
echo "============================================================"
echo ""

# --- 1. Docker 检测与引导安装 -------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    warn "未检测到 Docker。"
    if [ "$OS" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
        info "将使用 Homebrew 安装 Docker Desktop（可能耗时 5-10 分钟）"
        printf "继续安装？(Y/n) "
        read -r answer
        case "$answer" in
            [nN]*) fatal "已取消。手动安装：https://www.docker.com/products/docker-desktop/" ;;
        esac
        brew install --cask docker || fatal "brew 安装 Docker 失败，请手动安装后重试"
    else
        [ "$OS" = "Darwin" ] && open "https://www.docker.com/products/docker-desktop/" 2>/dev/null
        fatal "请先安装 Docker Desktop（https://www.docker.com/products/docker-desktop/）后重新运行"
    fi
fi

# --- 2. 启动引擎 ---------------------------------------------------------------
if ! docker info >/dev/null 2>&1; then
    info "Docker 已安装但引擎未运行，正在启动..."
    if [ "$OS" = "Darwin" ]; then
        open -a Docker 2>/dev/null || fatal "无法启动 Docker Desktop，请手动打开后重试"
    elif command -v systemctl >/dev/null 2>&1; then
        sudo systemctl start docker 2>/dev/null || warn "systemctl 启动失败，请确认 Docker 服务状态"
    fi
    waited=0
    while ! docker info >/dev/null 2>&1; do
        sleep 5
        waited=$((waited + 5))
        [ "$waited" -ge 240 ] && fatal "Docker 引擎 4 分钟内未就绪，请打开 Docker 确认状态后重试"
        if [ $((waited % 20)) -eq 0 ]; then info "等待 Docker 引擎启动... ${waited}s"; fi
    done
fi
ok "Docker 引擎运行中"

# --- 3. 下载最小文件集 -----------------------------------------------------------
mkdir -p "$HOME_DIR" || fatal "无法创建目录 $HOME_DIR"
cd "$HOME_DIR" || fatal "无法进入 $HOME_DIR"

fetch() {  # fetch <url> <dest>：curl 优先，wget 兜底
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$1" -o "$2"
    elif command -v wget >/dev/null 2>&1; then
        wget -q "$1" -O "$2"
    else
        fatal "需要 curl 或 wget 来下载部署文件"
    fi
}

# fetch_fallback <url> <dest>：主源失败时回退 jsdelivr（raw.githubusercontent.com
# 国内经常不通；jsdelivr 国内可直连）。仅对默认 GitHub raw 源生效，自定义
# AUGURA_REPO_RAW 的用户自己负责可达性。
fetch_fallback() {
    if fetch "$1" "$2"; then
        return 0
    fi
    case "$1" in
        https://raw.githubusercontent.com/*)
            local path="${1#https://raw.githubusercontent.com/}"
            # owner/repo/branch/path → cdn.jsdelivr.net/gh/owner/repo@branch/path
            local branch_path="${path#augura-os/augura/}"
            local mirror="https://cdn.jsdelivr.net/gh/augura-os/augura@${branch_path%%/*}/${branch_path#*/}"
            warn "主源下载失败，回退 jsdelivr 镜像：${mirror}"
            fetch "$mirror" "$2"
            ;;
        *)
            return 1
            ;;
    esac
}

info "下载部署文件到 $HOME_DIR ..."
fetch_fallback "$COMPOSE_URL" docker-compose.yml || fatal "docker-compose.yml 下载失败（${COMPOSE_URL}）"
fetch_fallback "$ENV_EXAMPLE_URL" .env.example || fatal ".env.example 下载失败（${ENV_EXAMPLE_URL}）"
ok "部署文件就绪"

# --- 4. 环境文件（首启生成随机密码） ---------------------------------------------
rand_pw() { LC_ALL=C tr -dc 'a-zA-Z0-9' </dev/urandom | head -c 24; }

if [ ! -f .env ]; then
    pg="$(rand_pw)"
    neo4j="$(rand_pw)"
    minio_root="$(rand_pw)"
    minio_app="$(rand_pw)"
    LC_ALL=C sed \
        -e "s/^POSTGRES_PASSWORD=augura$/POSTGRES_PASSWORD=$pg/" \
        -e "s|^NEO4J_AUTH=neo4j/augura123$|NEO4J_AUTH=neo4j/$neo4j|" \
        -e "s/^NEO4J_PASSWORD=augura123$/NEO4J_PASSWORD=$neo4j/" \
        -e "s/^MINIO_ROOT_PASSWORD=augura123$/MINIO_ROOT_PASSWORD=$minio_root/" \
        -e "s/^MINIO_APP_PASSWORD=changeme-app$/MINIO_APP_PASSWORD=$minio_app/" \
        .env.example > .env
    ok "已创建 .env（含随机生成的数据库密码；AI Key 可稍后在 Settings 页配置）"
else
    ok ".env 已存在"
fi

# --- 5. 启动服务（预构建镜像） ---------------------------------------------------
info "正在启动服务（首次拉取镜像约 2-3 分钟，视网络而定）..."

# ghcr.io 探测：不通则经镜像站 pull + retag（compose 文件保持 ghcr.io 单一事实源）。
# 注意用 curl 退出码而不是 HTTP 状态——/v2/ 正常也返回 401，能收到响应即算通。
GHCR_IMAGES="augura-os/augura-api:latest augura-os/augura-web:latest"
IMAGE_MIRROR="${AUGURA_IMAGE_MIRROR:-ghcr.nju.edu.cn}"
used_mirror=""
if ! curl -s -o /dev/null --max-time 5 https://ghcr.io/v2/ 2>/dev/null; then
    warn "ghcr.io 直连不通，改用镜像站 ${IMAGE_MIRROR}（可用 AUGURA_IMAGE_MIRROR 覆盖）"
    for img in $GHCR_IMAGES; do
        docker pull "${IMAGE_MIRROR}/${img}" \
            && docker tag "${IMAGE_MIRROR}/${img}" "ghcr.io/${img}" \
            || fatal "镜像站拉取失败：${IMAGE_MIRROR}/${img}——可换 AUGURA_IMAGE_MIRROR 重试，或 clone 仓库后 docker compose up --build -d 本地构建"
    done
    used_mirror=1
    ok "镜像已通过镜像站就绪"
fi

if [ -z "$used_mirror" ] && ! docker compose pull; then
    fatal "镜像拉取失败——若仓库尚未公开发布镜像，请 clone 仓库后用 docker compose up --build -d 本地构建"
fi
docker compose up -d || fatal "服务启动失败。查看日志：docker compose logs api"

# --- 6. 健康检查 -----------------------------------------------------------------
info "等待 API 就绪..."
ready=""
i=0
while [ "$i" -lt 60 ]; do
    if curl -fsS -o /dev/null --max-time 3 http://localhost:8000/docs 2>/dev/null; then
        ready=1
        break
    fi
    sleep 2
    i=$((i + 1))
done
[ -z "$ready" ] && fatal "API 120 秒内未就绪，请运行 docker compose logs api 排查"

# --- 7. 完成 ---------------------------------------------------------------------
echo ""
echo "============================================================"
echo "  Augura 已启动！"
echo "  Web:  http://localhost:3000"
echo ""
echo "  首次使用: Settings 配置 AI Key -> Upload 上传素材"
echo "  目录:   ${HOME_DIR}（重复运行本脚本即更新）"
echo "============================================================"
if [ "$OS" = "Darwin" ]; then
    open "http://localhost:3000" 2>/dev/null || true
elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://localhost:3000" 2>/dev/null || true
fi

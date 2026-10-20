#!/usr/bin/env bash
# AI Event Radar — 一键部署脚本（OCI Ubuntu ARM64 / 任何 Docker 主机）
# 用法:  bash scripts/deploy.sh
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.prod.yml"

log()  { echo -e "\033[1;32m[deploy]\033[0m $*"; }
fail() { echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; exit 1; }

log "1/9 检查系统与 Docker"
docker version >/dev/null 2>&1 || fail "docker 不可用，请先安装 Docker Engine 24+ 与 compose 插件"
uname -m | grep -Eq 'aarch64|arm64' && log "检测到 ARM64 主机" || log "检测到 x86_64 主机"

log "2/9 检查环境变量"
[ -f .env ] || fail "缺少 .env 文件：cp .env.example .env 后填写全部必填项"
set -a; . ./.env; set +a
: "${ENV:?未设置 ENV（生产必须 production）}"
: "${JWT_SECRET:?未设置 JWT_SECRET（>=32 位随机）}"
: "${ADMIN_PASSWORD:?未设置 ADMIN_PASSWORD（>=12 位）}"
: "${DB_PASSWORD:?未设置 DB_PASSWORD}"
: "${ALLOWED_ORIGINS:?未设置 ALLOWED_ORIGINS（如 https://your.domain）}"
for var in JWT_SECRET ADMIN_PASSWORD DB_PASSWORD; do
  val="${!var}"; [ ${#val} -ge 12 ] || fail "$var 长度不足（至少 12 位）"
done
[ "${#JWT_SECRET}" -ge 32 ] || fail "JWT_SECRET 至少 32 位"

log "3/9 检查磁盘空间"
avail_kb=$(df -Pk . | tail -1 | awk '{print $4}')
[ "$avail_kb" -ge 2097152 ] || fail "磁盘可用空间不足 2GB（当前 $((avail_kb/1024))MB）"

log "4/9 校验 compose 配置"
$COMPOSE config >/dev/null || fail "docker-compose.prod.yml 配置无效"

log "5/9 构建镜像（多阶段；ARM64 原生）"
$COMPOSE build

log "6/9 启动数据库并等待健康"
$COMPOSE up -d db
$COMPOSE ps db | grep -q healthy || fail "数据库未进入 healthy 状态：$COMPOSE ps db"

log "7/9 启动 API（先由独立 migrate 服务执行一次性 schema 迁移）与 Web"
$COMPOSE up -d api web
$COMPOSE ps migrate | grep -q "exited (0)" || fail "迁移服务未成功完成：$COMPOSE logs migrate"

log "8/9 等待健康检查通过"
base="http://localhost:${HTTP_PORT:-80}"
for i in $(seq 1 40); do
  if curl -fsS "$base/health/live" >/dev/null 2>&1; then
     if curl -fsS "$base/api/health/ready" | grep -q '"status":"ready"'; then
       log "健康检查通过"; break
     fi
  fi
  [ $i -eq 40 ] && fail "健康检查未通过（40 次重试后）：curl $base/health/live"
  sleep 3
done

log "9/9 启动后台采集 Worker（单调度实例，DB 领导锁）"
$COMPOSE up -d worker
sleep 2
$COMPOSE ps worker | grep -q Up || fail "worker 未启动：$COMPOSE logs worker"

log "验证调度状态（worker 侧 leader lock）"
status=$(curl -fsS "$base/api/status") || fail "无法读取 /status"
echo "$status" | grep -q '"scheduler_running": true' \
  && log "调度器已在 worker 进程运行（✅ leader 锁生效）" \
  || log "⚠️ /status 显示调度器未运行（正常现象：API 进程不持锁，锁在 worker）——使用 $COMPOSE logs worker 确认"

log "数据库种子数据源（幂等，重复执行不产生重复）"
$COMPOSE exec -T api python -m app.seed || true

log "部署完成 ✅"
echo "  控制台:  $base"
echo "  日志:    $COMPOSE logs -f [api|worker|web]"
echo "  健康:    bash scripts/healthcheck.sh"

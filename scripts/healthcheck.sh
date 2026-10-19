#!/usr/bin/env bash
# AI Event Radar — 健康检查（不伪造结果）
# 用法:  bash scripts/healthcheck.sh
set -uo pipefail
cd "$(dirname "$0")/.."
base="${base:-http://localhost:${HTTP_PORT:-80}}"
rc=0

check() {
  local name="$1" url="$2" expect="${3:-}"
  local out
  if ! out=$(curl -fsS --max-time 10 "$url" 2>&1); then
    echo "❌ $name 失败: $out"; rc=1; return
  fi
  if [ -n "$expect" ] && ! grep -q "$expect" <<<"$out"; then
    echo "❌ $name 内容异常: ${out:0:120}"; rc=1; return
  fi
  echo "✅ $name"
}

check "liveness (web进程)"     "$base/health/live"
check "readiness (数据库)"     "$base/api/health/ready" '"status":"ready"'
check "系统状态"               "$base/api/status"

echo "---- 采集 Worker 最近日志 ----"
docker compose -f docker-compose.prod.yml logs --tail 5 worker 2>/dev/null | tail -5 || echo "(worker 未以 compose 运行)"

echo "---- 数据库备份情况 ----"
ls -1t backups/radar_*.sql.gz 2>/dev/null | head -3 || echo "⚠️  尚未创建任何备份，请执行 bash scripts/backup.sh"

[ $rc -eq 0 ] && echo "健康检查全部通过 ✅" || echo "健康检查存在问题 ⚠️"
exit $rc

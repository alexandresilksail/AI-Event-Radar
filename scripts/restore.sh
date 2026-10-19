#!/usr/bin/env bash
# AI Event Radar — 数据库恢复（⚠️ 会覆盖当前数据库，执行前请再次确认）
# 用法:  bash scripts/restore.sh backups/radar_YYYYMMDD_HHMMSS.sql.gz
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

DUMP="$1"
[ -f "$DUMP" ] || { echo "备份文件不存在: $DUMP"; exit 1; }

echo "⚠️  即将用 $DUMP 覆盖数据库 ${DB_NAME:-radar}，此操作不可逆。"
read -r -p "输入 RESTORE 确认执行: " ans
[ "$ans" = "RESTORE" ] || { echo "已取消"; exit 1; }

docker compose -f docker-compose.prod.yml exec -T db \
  psql -U "${DB_USER:-radar}" -d "${DB_NAME:-radar}" < <(gunzip -c "$DUMP")
echo "恢复完成 ✅  建议执行: bash scripts/healthcheck.sh"

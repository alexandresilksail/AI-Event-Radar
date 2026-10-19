#!/usr/bin/env bash
# AI Event Radar — 数据库备份（PostgreSQL -> gz 文件）
# 用法:  bash scripts/backup.sh [输出目录]
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

OUT_DIR="${1:-./backups}"
mkdir -p "$OUT_DIR"
STAMP=$(date +%Y%m%d_%H%M%S)
OUT="$OUT_DIR/radar_${STAMP}.sql.gz"

docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U "${DB_USER:-radar}" -d "${DB_NAME:-radar}" --no-owner --clean --if-exists \
  | gzip > "$OUT"
ls -lh "$OUT" | awk '{print "备份完成: "$9" ("$5")"}'

# 保留最近 14 份
ls -1t "$OUT_DIR"/radar_*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
echo "保留策略: 最近 14 份"

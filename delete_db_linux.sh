#!/usr/bin/env bash
# ============================================================
# 生产环境重置数据库：删除两个空间的旧库，并按当前代码的表结构重建空库
#   化学实验室：chemicals.db
#   物理实验室：physics.db
# 无需重启服务 —— Flask 每个请求独立建立数据库连接，
# 重建出的空库立即可以被服务使用（相当于恢复出厂）。
# 用法：bash delete_db_linux.sh   （或 chmod +x 后直接 ./delete_db_linux.sh）
# 注意：表结构与 app.py 中 ensure_schema 的建表语句保持一致，
#       若 app.py 的 schema 有变动，请同步修改本脚本中的建表部分。
# ============================================================
set -e
cd "$(dirname "$0")"

echo '将重置全部数据库：化学 chemicals.db 与物理 physics.db'
echo '（清空两个空间的全部药品与登记记录，不可恢复）。'
printf '确认重置请输入 y 后回车，其他任意键取消：'
read -r answer
if [ "$answer" != 'y' ]; then
    echo '已取消，未做任何改动。'
    exit 0
fi

# 1. 删除旧库（含 WAL / SHM 附属文件）
rm -f chemicals.db chemicals.db-wal chemicals.db-shm \
      physics.db physics.db-wal physics.db-shm

# 2. 按当前格式为两个空间各重建一个空库
python3 - <<'PYEOF'
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS drugs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    unit          TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    low_threshold REAL
);

CREATE TABLE IF NOT EXISTS records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    drug_id     INTEGER NOT NULL REFERENCES drugs(id),
    change_type TEXT    NOT NULL CHECK (change_type IN ('in', 'use')),
    quantity    REAL    NOT NULL CHECK (quantity > 0),
    note        TEXT    NOT NULL DEFAULT '',
    operator    TEXT    NOT NULL DEFAULT '',
    revoked     INTEGER NOT NULL DEFAULT 0,
    revoked_by  TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_records_drug ON records(drug_id);
"""

for db_file in ("chemicals.db", "physics.db"):
    conn = sqlite3.connect(db_file)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    print("空库已按当前格式重建：" + db_file)
PYEOF

# 3. 若以 root 执行，统一数据库文件属主为 www（与服务目录内其他文件一致）
if [ "$(id -u)" -eq 0 ] && id www >/dev/null 2>&1; then
    chown www:www chemicals.db physics.db
fi

echo '完成：化学 / 物理两个空间的数据库均已重置为空库，无需重启服务。'

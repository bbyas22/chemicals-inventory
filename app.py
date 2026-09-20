# -*- coding: utf-8 -*-
"""
化学药品使用统计 Web 应用
=========================

后端：Python Flask + SQLite（标准库 sqlite3，轻量、免安装）
功能：
  - 药品入库 / 使用登记，登记时间自动记录（精确到分钟）
  - 实时库存计算（入库为正、使用为负）
  - 手动登记 + AI 辅助登记（AI 解析见 ai_client.py，调用方式同 test_ai.py）
  - 任何时候都可下载 Excel 模板批量导入入库（新药自动建档，老药追加入库）

启动：python app.py  （端口与硅基流动 API Key 见 config.py，也可用环境变量覆盖）
默认监听 127.0.0.1:5014，生产环境由 nginx 等反向代理转发。
"""

import io
import os
import sqlite3
from datetime import datetime, timedelta

from flask import (
    Flask, g, jsonify, render_template, request, send_file,
)
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from werkzeug.middleware.proxy_fix import ProxyFix

from ai_client import AICallError, AIConfigError, parse_chemical_text
from config import PORT, SILICONFLOW_API_KEY, USER_PASSWORD, ADMIN_PASSWORD

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "chemicals.db")

app = Flask(__name__)

# 反向代理基础支持（保留 X-Forwarded-* 头处理，无头时无副作用）
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)


@app.after_request
def _no_cache_html(resp):
    """HTML 页面禁用缓存：避免浏览器缓存旧版页面引用过期的静态资源。
    静态资源本身带版本号查询串，可正常缓存。"""
    if resp.content_type.startswith("text/html"):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp

# ------------------------------------------------------------
# 子路径兼容说明：本项目前端全部使用相对路径（页面链接、静态资源、API 请求），
# 反向代理挂在任意子路径（如 /lab/）下剥前缀转发即可正常工作，
# 后端不需要任何配置，页面内也不会产生任何重定向。
# ------------------------------------------------------------

# ------------------------------------------------------------
# Excel 批量入库导入：模板表头与识别别名
# ------------------------------------------------------------
IMPORT_SHEET_NAME = "药品入库导入"
HEADER_NAME = "药品名称"
HEADER_UNIT = "单位"
HEADER_QTY = "入库量"
HEADER_THRESHOLD = "低库存提醒"
# 各种可能的表头写法（用户可能改动表头文字；兼容早期“期初库存”版本模板）
HEADER_NAME_ALIASES = ("药品名称", "药品", "名称", "药品名")
HEADER_UNIT_ALIASES = ("单位", "计量单位")
HEADER_QTY_ALIASES = (
    "入库量", "入库量（期初库存）", "期初库存", "期初库存量",
    "库存", "库存量", "初始库存", "数量",
)
# 第 4 列可选：低于该数值时库存页标红并置顶（各种常见表头写法）
HEADER_THRESHOLD_ALIASES = (
    "低库存提醒", "低库存预警", "库存预警", "预警值", "预警数量",
    "最低库存", "安全库存", "补货提醒", "低库存阈值", "低于多少标红",
)
# 导入时未填提醒值：克 / 毫升默认 500 以下标红，其他单位不提醒
DEFAULT_THRESHOLD_BY_UNIT = {"克": 500.0, "毫升": 500.0}
MAX_IMPORT_ROWS = 1000


# ============================================================
# 数据库
# ============================================================

def get_db() -> sqlite3.Connection:
    """获取本次请求复用的数据库连接"""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now_minute() -> str:
    """当前时间，精确到分钟"""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def init_db():
    """建表（仅在数据库文件不存在时创建）；首次使用为空，等待 Excel 导入或手动建档"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS drugs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL UNIQUE,
            unit       TEXT    NOT NULL,
            created_at TEXT    NOT NULL
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
    )
    # 旧库迁移：补充 low_threshold 列（NULL=不提醒）；存量克/毫升药品默认 500
    cols = {r[1] for r in conn.execute("PRAGMA table_info(drugs)").fetchall()}
    if "low_threshold" not in cols:
        conn.execute("ALTER TABLE drugs ADD COLUMN low_threshold REAL")
        conn.execute(
            "UPDATE drugs SET low_threshold = 500 "
            "WHERE unit IN ('克', '毫升') AND low_threshold IS NULL"
        )
    # 旧库迁移：记录表补充操作人列（历史记录留空，前端展示为“—”）
    rcols = {r[1] for r in conn.execute("PRAGMA table_info(records)").fetchall()}
    if "operator" not in rcols:
        conn.execute("ALTER TABLE records ADD COLUMN operator TEXT NOT NULL DEFAULT ''")
    # 旧库迁移：撤销相关列。revoked: 0=正常记录；1=已被撤销（不计入库存）；
    # 2=撤销说明记录（只是说明谁撤销了哪条，不改变库存）
    if "revoked" not in rcols:
        conn.execute("ALTER TABLE records ADD COLUMN revoked INTEGER NOT NULL DEFAULT 0")
    if "revoked_by" not in rcols:
        conn.execute("ALTER TABLE records ADD COLUMN revoked_by TEXT NOT NULL DEFAULT ''")
    conn.commit()
    conn.close()


def default_threshold(unit):
    """未显式设置提醒值时的默认规则：克/毫升=500，其他单位=不提醒(None)"""
    return DEFAULT_THRESHOLD_BY_UNIT.get((unit or "").strip())


# ============================================================
# 查询辅助
# ============================================================

STOCK_SQL = (
    "SELECT d.id, d.name, d.unit, d.low_threshold, "
    "COALESCE(SUM(CASE WHEN r.change_type = 'in' THEN r.quantity "
    "                  WHEN r.change_type = 'use' THEN -r.quantity END), 0) AS stock "
    "FROM drugs d LEFT JOIN records r "
    "ON r.drug_id = d.id AND r.revoked = 0 "  # 已撤销记录与撤销说明不计入库存
    "GROUP BY d.id"
)

def query_drugs(db: sqlite3.Connection) -> list:
    rows = db.execute(STOCK_SQL + " ORDER BY d.name").fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "unit": row["unit"],
            "stock": round(row["stock"], 4),
            "low_threshold": row["low_threshold"],
        }
        for row in rows
    ]


def find_drug(db: sqlite3.Connection, drug_id=None, drug_name=None):
    if drug_id:
        return db.execute("SELECT * FROM drugs WHERE id = ?", (drug_id,)).fetchone()
    if drug_name:
        return db.execute(
            "SELECT * FROM drugs WHERE name = ?", (drug_name.strip(),)
        ).fetchone()
    return None


# ============================================================
# 页面
# ============================================================

# 默认进入登记页；系统为空（首次使用）时直接渲染库存页引导 Excel 导入。
# 不做重定向：反向代理子路径下浏览器地址栏保持不变，链接由相对路径解析。
@app.route("/")
def home():
    db = get_db()
    empty = db.execute("SELECT 1 FROM drugs LIMIT 1").fetchone() is None
    if empty:
        return inventory_page()
    return register_page()


@app.get("/register")
def register_page():
    """登记页面（默认页）：手动登记 + AI 辅助登记"""
    return render_template("register.html")


@app.get("/inventory")
def inventory_page():
    """实时库存页面"""
    return render_template("inventory.html")


@app.get("/records")
def records_page():
    """库存登记记录页面"""
    return render_template("records.html")


# ============================================================
# API：药品与库存
# ============================================================

@app.get("/api/drugs")
def api_list_drugs():
    """药品列表 + 实时库存（库存由入库/使用流水实时汇总）"""
    return jsonify(query_drugs(get_db()))


@app.post("/api/drugs/<int:drug_id>/threshold")
def api_set_threshold(drug_id):
    """
    在线设置药品的低库存提醒阈值（实时库存页的 ⚙ 图标入口）。
    请求 JSON：{"low_threshold": 500}；null 或空字符串表示不提醒。
    """
    db = get_db()
    drug = db.execute("SELECT id FROM drugs WHERE id = ?", (drug_id,)).fetchone()
    if drug is None:
        return jsonify({"error": "药品不存在"}), 404

    data = request.get_json(silent=True) or {}
    raw = data.get("low_threshold")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        threshold = None
    elif isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return jsonify({"error": "提醒阈值必须是大于 0 的数字，或留空表示不提醒"}), 400
    else:
        threshold = float(raw)
        if threshold <= 0:
            return jsonify({"error": "提醒阈值必须大于 0，或留空表示不提醒"}), 400

    db.execute("UPDATE drugs SET low_threshold = ? WHERE id = ?", (threshold, drug_id))
    db.commit()
    # 返回全量库存，前端据此立即刷新标红与置顶排序
    return jsonify({"id": drug_id, "low_threshold": threshold, "drugs": query_drugs(db)})


@app.get("/api/records")
def api_list_records():
    """
    登记记录查询（最新在前，JOIN 药品表）：
    - start / end：日期范围，格式 YYYY-MM-DD（end 包含当天）
    - limit / offset：分页，默认每页 50 条
    - all=1：返回范围内全部记录（前端会先飘窗提醒可能较慢）
    返回 {"records": [...], "total": 范围内总条数, "has_more": 后面是否还有}
    """
    where, params = [], []

    start = (request.args.get("start") or "").strip()
    end = (request.args.get("end") or "").strip()
    if start:
        try:
            datetime.strptime(start, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "start 日期格式应为 YYYY-MM-DD"}), 400
        where.append("r.created_at >= ?")
        params.append(start + "T00:00:00")
    if end:
        try:
            end_day = datetime.strptime(end, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "end 日期格式应为 YYYY-MM-DD"}), 400
        where.append("r.created_at < ?")
        params.append((end_day + timedelta(days=1)).strftime("%Y-%m-%d") + "T00:00:00")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    db = get_db()
    total = db.execute(
        "SELECT COUNT(*) FROM records r JOIN drugs d ON d.id = r.drug_id" + where_sql,
        params,
    ).fetchone()[0]

    if request.args.get("all") == "1":
        limit, offset = max(total, 1), 0
    else:
        limit = max(1, min(request.args.get("limit", default=50, type=int) or 50, 500))
        offset = max(0, request.args.get("offset", default=0, type=int) or 0)

    rows = db.execute(
        "SELECT r.id, r.drug_id, d.name, d.unit, r.change_type, "
        "r.quantity, r.note, r.operator, r.revoked, r.revoked_by, r.created_at "
        "FROM records r JOIN drugs d ON d.id = r.drug_id"
        + where_sql
        + " ORDER BY r.id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    returned = len(rows)
    return jsonify({
        "records": [dict(row) for row in rows],
        "total": total,
        "has_more": (offset + returned) < total,
    })


@app.post("/api/records/<int:record_id>/revoke")
def api_revoke_record(record_id):
    """
    管理员撤销一条登记记录：
    - 被撤销的记录本身保留，仅标记 revoked=1（不计入库存汇总）
    - 库存按该记录逆向恢复：入库的减回去，使用的加回去
    - 同时新增一条 revoked=2 的撤销说明记录（operator=撤销人），不改库存
    """
    db = get_db()
    data = request.get_json(silent=True) or {}
    operator = (data.get("operator") or "").strip()
    if not operator:
        return jsonify({"error": "缺少操作人，请先完成首次使用登记"}), 400

    rec = db.execute(
        "SELECT r.id, r.drug_id, r.change_type, r.quantity, r.operator, r.revoked, "
        "d.name, d.unit FROM records r JOIN drugs d ON d.id = r.drug_id "
        "WHERE r.id = ?",
        (record_id,),
    ).fetchone()
    if rec is None:
        return jsonify({"error": "记录不存在"}), 404
    if rec["revoked"] != 0:
        return jsonify({"error": "该记录已被撤销，不能重复操作"}), 400

    ts = datetime.now().isoformat(timespec="seconds")
    db.execute(
        "UPDATE records SET revoked = 1, revoked_by = ? WHERE id = ?",
        (operator, record_id),
    )
    # 撤销说明记录：change_type/quantity 仅满足表约束，revoked=2 不参与库存
    qty = rec["quantity"]
    qty_text = ("%g" % qty) if qty == int(qty) else str(qty)
    summary = (
        "%s %s%s" % ("入库" if rec["change_type"] == "in" else "使用",
                     qty_text, rec["unit"])
    )
    db.execute(
        "INSERT INTO records (drug_id, change_type, quantity, note, operator, "
        "revoked, created_at) VALUES (?, 'use', 1, ?, ?, 2, ?)",
        (
            rec["drug_id"],
            "撤销了 #%d 的登记（%s，操作人 %s）" % (record_id, summary, rec["operator"] or "—"),
            operator,
            ts,
        ),
    )
    db.commit()
    return jsonify({"ok": True, "id": record_id})


# ============================================================
# API：首次使用身份登记（真实姓名存浏览器本地，首用需校验密码）
# 普通用户密码 / 管理员密码统一在 config.py 配置（也可用环境变量覆盖）
# ============================================================


@app.post("/api/user/verify")
def api_user_verify():
    """首次使用登记：校验管理密码。姓名只保存在浏览器本地，之后作为操作人随每次登记提交。"""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    password = str(data.get("password") or "")
    if not name:
        return jsonify({"error": "请填写真实姓名"}), 400
    if len(name) > 20:
        return jsonify({"error": "姓名不能超过 20 个字"}), 400
    if password == USER_PASSWORD:
        role = "user"
    elif password.upper() == ADMIN_PASSWORD:
        role = "admin"
    else:
        return jsonify({"error": "管理密码错误，请重新输入"}), 401
    return jsonify({"ok": True, "name": name, "role": role})


# ============================================================
# API：Excel 批量入库导入（任何时候都可用）
# ============================================================

def _build_template_workbook() -> bytes:
    """生成 Excel 批量入库导入模板：一个填写表 + 一个填写说明表"""
    wb = Workbook()
    ws = wb.active
    ws.title = IMPORT_SHEET_NAME

    headers = [HEADER_NAME, HEADER_UNIT, HEADER_QTY, HEADER_THRESHOLD]
    ws.append(headers)
    for col, _ in enumerate(headers, start=1):
        ws.cell(row=1, column=col).font = Font(bold=True)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 10
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 16
    ws.freeze_panes = "A2"

    guide = wb.create_sheet("填写说明")
    tips = [
        "化学药品 Excel 入库导入模板 — 填写说明",
        "",
        "1. 请在「药品入库导入」工作表中从第 2 行开始逐行填写，每行一种药品。",
        "2. 各列含义：",
        "   · 药品名称：药品的标准名称，不可为空，表内不可重复（如：氯化钠）。",
        "   · 单位：计量单位，不可为空（如：克、毫克、毫升、升、瓶、盒）。",
        "   · 入库量：本次入库的数量，必须是大于 0 的数字（如：500）。",
        "   · 低库存提醒：可留空。填一个数字（如 500），剩余库存低于它时，",
        "     在「实时库存」页会标红并排在最前面，方便一眼看到不足的药品。",
        "3. 低库存提醒留空时的默认规则：单位为「克」或「毫升」的按 500 提醒；",
        "   瓶、盒等其他单位不提醒（留空即为不提醒）。",
        "4. 不要修改第 1 行的表头；不要合并单元格。",
        "5. 一次最多导入 1000 行；填好后保存为 .xlsx 文件并在网页中上传。",
        "6. 任何时候都可以导入：库中没有的药品会自动建档并入库；",
        "   已有的药品会按相同单位追加一笔入库，单位与库中不一致的行会被标出。",
        "   再次导入时，已存在药品的提醒值：本行填了就更新，留空则保持原值不变。",
    ]
    for row in tips:
        guide.append([row])
    guide.column_dimensions["A"].width = 72
    guide.cell(row=1, column=1).font = Font(bold=True)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


@app.get("/api/import/template")
def api_download_template():
    """下载 Excel 导入模板（.xlsx）"""
    data = _build_template_workbook()
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name="化学药品入库导入模板.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _match_header(header_row: dict):
    """
    在表头行中按别名定位列号（从 1 开始）。
    返回 (name_col, unit_col, qty_col, threshold_col)；前三个必填，
    threshold_col 可选（找不到为 None）。必填列缺失返回 None。
    """
    def norm(v):
        return str(v or "").replace(" ", "").replace("　", "").strip()

    # header_row 形如 {表头文字: 列号}
    cols = {norm(text): col for text, col in header_row.items() if norm(text)}
    used = set()  # 已被占用的列，避免模糊匹配串列（如“库存”误中“低库存提醒”）

    def find_col(aliases, required=True):
        # 先精确匹配
        for alias in aliases:
            idx = cols.get(alias)
            if idx and idx not in used:
                used.add(idx)
                return idx
        # 容错：表头包含关键字即可（跳过已被其他列占用的列）
        for text, idx in cols.items():
            if idx not in used and any(a in text for a in aliases):
                used.add(idx)
                return idx
        if required:
            return None
        return None

    name_col = find_col(HEADER_NAME_ALIASES)
    unit_col = find_col(HEADER_UNIT_ALIASES)
    qty_col = find_col(HEADER_QTY_ALIASES)
    if not (name_col and unit_col and qty_col):
        return None
    # 可选列：注意它含“库存”字样，必须在入库量列之后匹配，且跳过已占用列
    threshold_col = find_col(HEADER_THRESHOLD_ALIASES, required=False)
    return name_col, unit_col, qty_col, threshold_col


@app.post("/api/import/excel")
def api_import_excel():
    """
    上传填好的 .xlsx 批量入库（任何时候都可用）：
      - 库中没有的药品：按表中单位建档并写入一笔入库；
      - 库中已有的药品：单位一致时追加一笔入库，单位不一致该行报错；
      - 表内药品名称重复、数量非法等行报错。
    任一行校验失败则整批不写入，返回 400 与逐行错误信息，修改后重新上传即可。
    """
    db = get_db()

    operator = (request.form.get("operator") or "").strip()
    if not operator:
        return jsonify({"error": "缺少操作人，请刷新页面完成首次身份登记"}), 400

    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"error": "请选择要导入的 .xlsx 文件"}), 400
    if not file.filename.lower().endswith(".xlsx"):
        return jsonify({"error": "仅支持 .xlsx 格式，请使用模板填写后另存为 xlsx"}), 400

    try:
        wb = load_workbook(file, data_only=True, read_only=True)
    except Exception:
        return jsonify({"error": "文件无法解析，请确认是用本模板填写的有效 Excel 文件"}), 400

    # 优先找模板工作表，否则取第一个工作表
    ws = wb[IMPORT_SHEET_NAME] if IMPORT_SHEET_NAME in wb.sheetnames else wb.worksheets[0]

    # 在前 5 行内定位表头
    located = None
    header_row_no = 0
    rows_iter = ws.iter_rows(values_only=False)
    for i, row in enumerate(rows_iter, start=1):
        # read_only 模式下尾部空单元格是 EmptyCell（无 column 属性），用枚举给列号
        values = {}
        for col_idx, c in enumerate(row, start=1):
            if getattr(c, "value", None) is not None:
                values[c.value] = col_idx
        located = _match_header(values)
        if located:
            header_row_no = i
            break
        if i >= 5:
            break

    if not located:
        return jsonify({
            "error": "未找到正确的表头，请重新下载模板并保留第 1 行"
                     "（药品名称 / 单位 / 入库量）",
        }), 400

    name_col, unit_col, qty_col, threshold_col = located
    items = []
    errors = []
    seen_names = set()
    data_cols = [c for c in (name_col, unit_col, qty_col, threshold_col) if c]

    def cell(row, col):
        return row[col - 1] if col - 1 < len(row) else None

    # offset 对所有行（含空行）计数，因此 header_row_no + offset 即真实 Excel 行号
    for offset, row in enumerate(
        ws.iter_rows(min_row=header_row_no + 1, values_only=True), start=1
    ):
        excel_row = header_row_no + offset
        raw_name = cell(row, name_col)
        raw_unit = cell(row, unit_col)
        raw_qty = cell(row, qty_col)
        raw_threshold = cell(row, threshold_col) if threshold_col else None

        # 整行为空：跳过（不计错误、不占条目）
        if all(cell(row, c) is None or str(cell(row, c)).strip() == ""
               for c in data_cols):
            continue

        name = str(raw_name).strip() if raw_name is not None else ""
        unit = str(raw_unit).strip() if raw_unit is not None else ""
        row_msgs = []

        existing = None
        if not name:
            row_msgs.append("药品名称为空")
        elif name in seen_names:
            row_msgs.append("药品名称「%s」在表内重复" % name)
        else:
            existing = db.execute(
                "SELECT id, unit, low_threshold FROM drugs WHERE name = ?", (name,)
            ).fetchone()

        if not unit:
            row_msgs.append("单位为空")
        elif existing is not None and existing["unit"] != unit:
            row_msgs.append(
                "药品「%s」库中单位为「%s」，与表中「%s」不一致"
                % (name, existing["unit"], unit)
            )

        qty = None
        if raw_qty is None or str(raw_qty).strip() == "":
            row_msgs.append("入库量为空")
        elif isinstance(raw_qty, bool) or not isinstance(raw_qty, (int, float)):
            row_msgs.append("入库量必须是数字（不能带文字或单位）")
        else:
            qty = float(raw_qty)
            if qty <= 0:
                row_msgs.append("入库量必须大于 0")

        # 低库存提醒：可选；填了就必须是大于 0 的数字
        threshold = None
        if threshold_col and not (
            raw_threshold is None or str(raw_threshold).strip() == ""
        ):
            if isinstance(raw_threshold, bool) or not isinstance(
                raw_threshold, (int, float)
            ):
                row_msgs.append("低库存提醒必须是数字（不能带文字），或留空")
            else:
                threshold = float(raw_threshold)
                if threshold <= 0:
                    row_msgs.append("低库存提醒必须大于 0，或留空")

        if row_msgs:
            errors.append({"row": excel_row, "name": name, "messages": row_msgs})
        else:
            seen_names.add(name)
            items.append({
                "name": name,
                "unit": unit,
                "quantity": qty,
                "threshold": threshold,  # None 表示本行未填
                "drug_id": existing["id"] if existing is not None else None,
                "old_threshold":
                    existing["low_threshold"] if existing is not None else None,
            })

        if len(items) + len(errors) >= MAX_IMPORT_ROWS:
            errors.append({
                "row": excel_row,
                "name": "",
                "messages": ["超过单次最多 %d 行的限制" % MAX_IMPORT_ROWS],
            })
            break

    if errors:
        return jsonify({
            "error": "Excel 中有 %d 行内容不符合要求，已全部取消导入，请修改后重新上传"
                     % len(errors),
            "errors": errors,
        }), 400

    if not items:
        return jsonify({"error": "没有读取到任何药品数据，请在表头下方填写后再导入"}), 400

    # 全部校验通过：事务写入。新药建档 + 每种药品一笔备注为“Excel导入”的入库流水
    # 提醒值规则：新药填了用填的、没填按单位默认；老药填了则更新、没填保持原值
    ts = now_minute()
    created = 0
    appended = 0
    try:
        for it in items:
            if it["drug_id"] is not None:
                drug_id = it["drug_id"]
                appended += 1
                if it["threshold"] is not None:
                    db.execute(
                        "UPDATE drugs SET low_threshold = ? WHERE id = ?",
                        (it["threshold"], drug_id),
                    )
            else:
                threshold = (
                    it["threshold"]
                    if it["threshold"] is not None
                    else default_threshold(it["unit"])
                )
                cur = db.execute(
                    "INSERT INTO drugs (name, unit, low_threshold, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (it["name"], it["unit"], threshold, ts),
                )
                drug_id = cur.lastrowid
                created += 1
            db.execute(
                "INSERT INTO records (drug_id, change_type, quantity, note, operator, created_at) "
                "VALUES (?, 'in', ?, ?, ?, ?)",
                (drug_id, it["quantity"], "Excel导入", operator, ts),
            )
        db.commit()
    except sqlite3.Error as exc:
        db.rollback()
        return jsonify({"error": "导入失败（数据库错误）：%s" % exc}), 400

    return jsonify({
        "imported": len(items),
        "created": created,
        "appended": appended,
        "drugs": query_drugs(db),
    }), 201


# ============================================================
# API：登记（入库 / 使用）
# ============================================================

@app.post("/api/records")
def api_create_record():
    """
    新增一条登记记录。
    请求 JSON：
      change_type: "in" | "use"
      drug_id:     已有药品 id（优先）
      drug_name:   药品名称（drug_id 为空时使用；入库时库中不存在则自动建档）
      unit:        新建档药品的单位（仅入库且药品不存在时需要）
      quantity:    数量（正数）
      note:        备注（可选）
      operator:    操作人（首次登记后由前端自动携带，必填）
    """
    data = request.get_json(silent=True) or {}
    operator = (data.get("operator") or "").strip()
    if not operator:
        return jsonify({"error": "缺少操作人，请刷新页面完成首次身份登记"}), 400

    change_type = data.get("change_type")
    if change_type not in ("in", "use"):
        return jsonify({"error": "登记类型必须是 in（入库）或 use（使用）"}), 400

    try:
        quantity = float(data.get("quantity"))
    except (TypeError, ValueError):
        return jsonify({"error": "数量必须是有效的正数"}), 400
    if quantity <= 0:
        return jsonify({"error": "数量必须大于 0"}), 400

    drug_id = data.get("drug_id")
    drug_name = (data.get("drug_name") or "").strip()
    unit = (data.get("unit") or "").strip()
    note = (data.get("note") or "").strip()

    db = get_db()
    drug = find_drug(db, drug_id=drug_id, drug_name=drug_name)

    # 库中不存在的药品：仅允许“入库”时携带单位新建档案
    if drug is None:
        if change_type != "in":
            return jsonify({"error": "库中没有该药品，无法登记使用；请先入库登记"}), 400
        if not drug_name:
            return jsonify({"error": "请填写药品名称"}), 400
        if not unit:
            return jsonify({"error": "新药品入库需要填写计量单位"}), 400
        ts = now_minute()
        cur = db.execute(
            "INSERT INTO drugs (name, unit, low_threshold, created_at) "
            "VALUES (?, ?, ?, ?)",
            (drug_name, unit, default_threshold(unit), ts),
        )
        drug_id = cur.lastrowid
        drug = db.execute("SELECT * FROM drugs WHERE id = ?", (drug_id,)).fetchone()

    # 使用登记：校验库存是否充足，保证库存数据准确
    if change_type == "use":
        stock_row = db.execute(
            "SELECT COALESCE(SUM(CASE WHEN change_type = 'in' THEN quantity "
            "                        WHEN change_type = 'use' THEN -quantity END), 0) AS s "
            "FROM records WHERE drug_id = ?",
            (drug["id"],),
        ).fetchone()
        if quantity > stock_row["s"]:
            return jsonify(
                {
                    "error": f"库存不足：{drug['name']}当前剩余 "
                             f"{round(stock_row['s'], 4)} {drug['unit']}"
                }
            ), 400

    ts = now_minute()
    cur = db.execute(
        "INSERT INTO records (drug_id, change_type, quantity, note, operator, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (drug["id"], change_type, quantity, note, operator, ts),
    )
    db.commit()

    record = {
        "id": cur.lastrowid,
        "drug_id": drug["id"],
        "name": drug["name"],
        "unit": drug["unit"],
        "change_type": change_type,
        "quantity": quantity,
        "note": note,
        "operator": operator,
        "created_at": ts,
    }
    # 返回登记结果 + 最新全部库存，前端据此立即刷新
    return jsonify({"record": record, "drugs": query_drugs(db)}), 201


# ============================================================
# API：AI 辅助登记（自然语言 -> 结构化数据，供用户预登记确认）
# ============================================================

def _match_drug(pname: str, drugs: list):
    """与药品库匹配：精确匹配优先，其次包含关系匹配"""
    for d in drugs:
        if d["name"] == pname:
            return d
    for d in drugs:
        if d["name"] in pname or pname in d["name"]:
            return d
    return None


@app.post("/api/ai/parse")
def api_ai_parse():
    """
    解析自然语言描述（一句话可包含多条入库/使用信息），
    返回结构化预登记数据列表 items，每条带独立的逐字段 errors。
    请求 JSON：{"text": "做实验用了 5 克氯化钠和 10 毫升盐酸"}
    """
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "请输入需要 AI 解析的描述内容"}), 400

    db = get_db()
    drugs = query_drugs(db)
    names = [d["name"] for d in drugs]

    try:
        parsed_items = parse_chemical_text(text, names)
    except AIConfigError as exc:
        return jsonify({"error": str(exc), "code": "NO_API_KEY"}), 503
    except AICallError as exc:
        return jsonify({"error": str(exc), "code": "AI_ERROR"}), 502

    # 逐条匹配药品库并逐字段业务校验：无问题字段正常展示，
    # 有问题字段在 errors 中给出原因，前端据此标红、未修正前不允许入库
    items_out = []
    for parsed in parsed_items:
        result = dict(parsed)
        errors = {}
        matched = _match_drug(parsed["drug_name"], drugs)

        if matched:
            # 已匹配药品：以库中标准名称和计量单位为准，避免单位口径混乱
            result["drug_id"] = matched["id"]
            result["drug_name"] = matched["name"]
            result["unit"] = matched["unit"]
            result["stock"] = matched["stock"]
            result["matched"] = True
        elif parsed["change_type"] == "in":
            # 入库允许库外药品：按 AI 识别的名称与单位提交时自动建档
            result["drug_id"] = None
            result["stock"] = None
            result["matched"] = False
            result["will_create"] = True
            if not (parsed.get("unit") or "").strip():
                errors["unit"] = "未识别出计量单位，新药品入库需要填写单位"
        else:
            result["drug_id"] = None
            result["stock"] = None
            result["matched"] = False
            errors["drug_name"] = (
                f"药品库中不存在「{parsed['drug_name']}」，请修改为库中药品"
            )

        quantity = parsed["quantity"]
        if quantity is None or quantity <= 0:
            errors["quantity"] = "未识别出有效数量，请填写大于 0 的数字"
        elif matched and parsed["change_type"] == "use" and quantity > matched["stock"]:
            errors["stock"] = (
                f"使用量超出库存：{matched['name']}当前仅剩 "
                f"{round(matched['stock'], 4)} {matched['unit']}"
            )

        result["errors"] = errors
        result["valid"] = not errors
        items_out.append(result)

    return jsonify({"raw_text": text, "items": items_out})


# ============================================================
# API：批量登记（AI 一句话多条，整体原子提交）
# ============================================================

class BatchError(Exception):
    """批量登记中的业务校验错误"""


@app.post("/api/records/batch")
def api_create_records_batch():
    """
    原子批量登记：所有条目在同一事务内提交，任一条失败则全部回滚。
    同一药品在批次内多次使用时，按批次内累计扣减校验库存。
    请求 JSON：
      {"items": [
          {"change_type": "in|use", "drug_id": 1, "quantity": 5, "note": ""},
          ...
      ],
       "operator": "操作人姓名"}
    """
    payload = request.get_json(silent=True) or {}
    operator = (payload.get("operator") or "").strip()
    if not operator:
        return jsonify({"error": "缺少操作人，请刷新页面完成首次身份登记"}), 400
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return jsonify({"error": "提交内容为空"}), 400
    if len(raw_items) > 50:
        return jsonify({"error": "单次最多登记 50 条"}), 400

    db = get_db()
    ts = now_minute()
    # 批次内每种药品的预期剩余量（同药品多条使用时累计扣减）
    projected = {}
    records = []

    def stock_of(drug_id):
        if drug_id not in projected:
            row = db.execute(
                "SELECT COALESCE(SUM(CASE WHEN change_type = 'in' THEN quantity "
                "                        WHEN change_type = 'use' THEN -quantity END), 0) AS s "
                "FROM records WHERE drug_id = ?",
                (drug_id,),
            ).fetchone()
            projected[drug_id] = row["s"]
        return projected[drug_id]

    try:
        for index, data in enumerate(raw_items, 1):
            if not isinstance(data, dict):
                raise BatchError(f"第 {index} 条数据格式不正确")

            change_type = data.get("change_type")
            if change_type not in ("in", "use"):
                raise BatchError(f"第 {index} 条：登记类型必须是 in 或 use")

            try:
                quantity = float(data.get("quantity"))
            except (TypeError, ValueError):
                raise BatchError(f"第 {index} 条：数量必须是有效的正数")
            if quantity <= 0:
                raise BatchError(f"第 {index} 条：数量必须大于 0")

            drug = find_drug(db, drug_id=data.get("drug_id"),
                             drug_name=str(data.get("drug_name", "") or "").strip())
            if drug is None:
                # 库中不存在的药品：仅允许“入库”时携带单位新建档案
                new_name = str(data.get("drug_name", "") or "").strip()
                new_unit = str(data.get("unit", "") or "").strip()
                if change_type != "in":
                    raise BatchError(
                        f"第 {index} 条：库中没有「{new_name}」，无法登记使用；请先入库建档"
                    )
                if not new_name:
                    raise BatchError(f"第 {index} 条：请填写药品名称")
                if not new_unit:
                    raise BatchError(f"第 {index} 条：新药品入库需要填写计量单位")
                cur = db.execute(
                    "INSERT INTO drugs (name, unit, low_threshold, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (new_name, new_unit, default_threshold(new_unit), ts),
                )
                new_id = cur.lastrowid
                drug = {"id": new_id, "name": new_name, "unit": new_unit}

            note = str(data.get("note", "") or "").strip()[:100]

            current = stock_of(drug["id"])
            if change_type == "use":
                if quantity > current:
                    raise BatchError(
                        f"第 {index} 条库存不足：{drug['name']}当前剩余 "
                        f"{round(current, 4)} {drug['unit']}"
                    )
                projected[drug["id"]] = current - quantity
            else:
                projected[drug["id"]] = current + quantity

            cur = db.execute(
                "INSERT INTO records (drug_id, change_type, quantity, note, operator, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (drug["id"], change_type, quantity, note, operator, ts),
            )
            records.append(
                {
                    "id": cur.lastrowid,
                    "drug_id": drug["id"],
                    "name": drug["name"],
                    "unit": drug["unit"],
                    "change_type": change_type,
                    "quantity": quantity,
                    "note": note,
                    "operator": operator,
                    "created_at": ts,
                }
            )

        db.commit()
    except BatchError as exc:
        db.rollback()
        return jsonify({"error": str(exc)}), 400

    return jsonify({"records": records, "drugs": query_drugs(db)}), 201


if __name__ == "__main__":
    init_db()
    # 端口统一在 config.py 配置（也可用环境变量 PORT 覆盖）；
    # 默认仅监听本机 127.0.0.1（生产环境由 nginx 反代转发，无需直接对外监听）
    print(" * 化学药品使用统计系统已启动")
    print(" * 本机访问：  http://127.0.0.1:%d/" % PORT)
    print(" * AI 接口 Key：" + ("已配置（AI 辅助登记可用）" if SILICONFLOW_API_KEY else "未配置（AI 辅助登记不可用，可在 config.py 填写）"))
    if not USER_PASSWORD or not ADMIN_PASSWORD:
        print(" * 警告：普通用户密码或管理员密码为空，任何人都可能完成首次登记，"
              "请复制 config.example.py 为 config.py 并填写密码")
    if USER_PASSWORD.upper() == ADMIN_PASSWORD:
        print(" * 警告：普通用户密码与管理员密码相同，将没有人能获得管理员身份，请在 config.py 修改")

    # 仅监听本机回环地址；关闭重载器避免 Windows 下产生孤儿进程
    app.run(host="127.0.0.1", port=PORT, debug=True, use_reloader=False)

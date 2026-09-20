/* ============================================================
   库存登记记录页面 + 管理员撤销
   记录查询：默认最近 7 天；「加载更多」每次追加 50 条；
             可按日期范围查询；「查看全部记录」飘窗确认后一次拉全量。
   ============================================================ */

var PAGE_SIZE = 50;

// 当前查询状态
var recState = {
  start: "",      // YYYY-MM-DD，空表示不限
  end: "",        // YYYY-MM-DD，空表示不限
  all: false,     // 是否一次加载全部
  offset: 0,      // 已加载条数（分页偏移）
  total: 0,       // 当前条件下的总条数
  loading: false,
};

function fmtDate(d) {
  var m = String(d.getMonth() + 1).padStart(2, "0");
  var day = String(d.getDate()).padStart(2, "0");
  return d.getFullYear() + "-" + m + "-" + day;
}

// 最近 7 天（含今天）
function defaultStartDate() {
  var d = new Date();
  d.setDate(d.getDate() - 6);
  return fmtDate(d);
}

function buildRecordsUrl(append) {
  var q = [];
  if (recState.all) {
    q.push("all=1");
  } else {
    if (recState.start) q.push("start=" + encodeURIComponent(recState.start));
    if (recState.end) q.push("end=" + encodeURIComponent(recState.end));
    q.push("limit=" + PAGE_SIZE);
    q.push("offset=" + (append ? recState.offset : 0));
  }
  return "/api/records?" + q.join("&");
}

function rangeText() {
  if (recState.all) return "全部时间";
  if (recState.start && recState.end) return recState.start + " 至 " + recState.end;
  if (recState.start) return recState.start + " 起";
  if (recState.end) return "截至 " + recState.end;
  return "全部时间";
}

function updateMeta() {
  var meta = $("#recMeta");
  if (!recState.total) {
    meta.textContent = "";
    return;
  }
  var shown = recState.all ? recState.total : Math.min(recState.offset, recState.total);
  var t = rangeText() + "：共 " + recState.total + " 条";
  if (!recState.all && shown < recState.total) {
    t += "，已显示 " + shown + " 条";
  }
  meta.textContent = t;
}

function setLoading(on) {
  recState.loading = on;
  ["recQuery", "recWeek", "recShowAll", "recLoadMore", "refreshRecords"].forEach(function (id) {
    var el = $("#" + id);
    if (el) el.disabled = on;
  });
}

// 单行 HTML
function recordRowHtml(r) {
  var admin = isAdmin();

  // 撤销说明记录（revoked=2）：不显示数量
  if (r.revoked === 2) {
    return '<tr class="row-revoke-note">' +
      "<td>" + r.created_at + "</td>" +
      '<td colspan="3" class="revoke-text">' + escapeHtml(r.note) + "</td>" +
      "<td>" + (r.operator ? escapeHtml(r.operator) : "—") + "</td>" +
      "<td>—</td>" +
      "<td></td>" +
      "</tr>";
  }

  var badge = r.change_type === "in"
    ? '<span class="badge badge-in">入库</span>'
    : '<span class="badge badge-use">使用</span>';
  var sign = r.change_type === "in" ? "+" : "-";
  var note = r.note ? escapeHtml(r.note) : "—";
  var operator = r.operator ? escapeHtml(r.operator) : "—";

  var rowCls = "";
  if (r.revoked === 1) {
    rowCls = ' class="row-revoked"';
    badge += ' <span class="revoked-tag">已被 '
      + escapeHtml(r.revoked_by || "管理员") + " 撤销</span>";
  }

  var act = "";
  if (admin && r.revoked === 0) {
    act = '<button type="button" class="icon-btn" data-act="revoke" data-id="'
      + r.id + '" title="撤销这条登记，库存将按该记录恢复">✕</button>';
  }

  return "<tr" + rowCls + ">" +
    "<td>" + r.created_at + "</td>" +
    "<td>" + escapeHtml(r.name) + "</td>" +
    "<td>" + badge + "</td>" +
    "<td>" + sign + formatQty(r.quantity) + " " + escapeHtml(r.unit) + "</td>" +
    "<td>" + operator + "</td>" +
    "<td>" + note + "</td>" +
    '<td class="col-act">' + act + "</td>" +
    "</tr>";
}

function renderRecords(rows) {
  $("#recordBody").innerHTML = rows.map(recordRowHtml).join("");
}

// append=false：按当前条件重新查第一页；true：追加下一页
async function fetchRecords(append) {
  if (recState.loading) return;
  var body = $("#recordBody");
  setLoading(true);
  var loadingRow = null;
  if (!append) {
    body.innerHTML = '<tr><td colspan="7" class="empty">加载中…</td></tr>';
  } else {
    loadingRow = document.createElement("tr");
    loadingRow.innerHTML = '<td colspan="7" class="empty">正在加载更多…</td>';
    body.appendChild(loadingRow);
  }

  try {
    var data = await api(buildRecordsUrl(append));
    var rows = data.records || [];
    recState.total = data.total || 0;
    if (loadingRow) loadingRow.remove();

    if (!append) {
      if (!rows.length) {
        body.innerHTML = '<tr><td colspan="7" class="empty">'
          + (rangeText() === "全部时间"
              ? "暂无登记记录"
              : rangeText() + " 范围内暂无登记记录")
          + "</td></tr>";
      } else {
        renderRecords(rows);
      }
    } else {
      body.insertAdjacentHTML("beforeend", rows.map(recordRowHtml).join(""));
    }

    if (!recState.all) {
      recState.offset = append ? recState.offset + rows.length : rows.length;
    }
    updateMeta();
    $("#recLoadMore").classList.toggle("hidden", recState.all || !data.has_more);
  } catch (err) {
    if (loadingRow) loadingRow.remove();
    if (!append) {
      body.innerHTML =
        '<tr><td colspan="7" class="empty">记录加载失败：' + escapeHtml(err.message) + "</td></tr>";
    }
    toast(err.message || "记录加载失败", "error");
  } finally {
    setLoading(false);
  }
}

// 按当前日期输入框发起查询
function queryByDate() {
  var s = $("#recStart").value;
  var e = $("#recEnd").value;
  if (s && e && s > e) {
    toast("开始日期不能晚于结束日期", "error");
    return;
  }
  recState.start = s;
  recState.end = e;
  recState.all = false;
  recState.offset = 0;
  fetchRecords(false);
}

// 恢复到默认的最近 7 天
function queryLastWeek() {
  recState.all = false;
  recState.start = defaultStartDate();
  recState.end = "";
  recState.offset = 0;
  $("#recStart").value = recState.start;
  $("#recEnd").value = "";
  fetchRecords(false);
}

// 查看全部：先飘窗提醒记录多时会慢
function queryAll() {
  if (!confirm("将一次性加载全部登记记录。\n如果记录较多，加载和页面渲染可能会较慢，确定继续吗？")) {
    return;
  }
  recState.all = true;
  recState.start = "";
  recState.end = "";
  recState.offset = 0;
  $("#recStart").value = "";
  $("#recEnd").value = "";
  fetchRecords(false);
}

async function revokeRecord(recordId) {
  var operator = currentOperator();
  if (!operator) {
    toast("请先完成首次使用登记", "error");
    return;
  }
  if (!confirm("确定撤销这条登记吗？\n库存将按该记录逆向恢复（入库减回、使用加回），操作不可再撤销。")) {
    return;
  }
  try {
    await api("/api/records/" + recordId + "/revoke", {
      method: "POST",
      body: JSON.stringify({ operator: operator }),
    });
    toast("已撤销 #" + recordId + "，库存已恢复", "success");
    fetchRecords(false);
  } catch (err) {
    toast(err.message || "撤销失败", "error");
  }
}

$("#recordBody").addEventListener("click", function (e) {
  var btn = e.target.closest(".icon-btn[data-act=revoke]");
  if (btn) revokeRecord(Number(btn.getAttribute("data-id")));
});

$("#refreshRecords").addEventListener("click", function () { fetchRecords(false); });
$("#recQuery").addEventListener("click", queryByDate);
$("#recWeek").addEventListener("click", queryLastWeek);
$("#recShowAll").addEventListener("click", queryAll);
$("#recLoadMore").addEventListener("click", function () { fetchRecords(true); });

// 默认进入：最近 7 天
recState.start = defaultStartDate();
$("#recStart").value = recState.start;
fetchRecords(false);

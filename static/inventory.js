/* ============================================================
   实时库存页面 + Excel 批量入库导入
   ============================================================ */

function renderStock() {
  var keyword = $("#stockSearch").value.trim();
  var list = sortedDrugs().filter(function (d) {
    return d.name.indexOf(keyword) !== -1;
  });

  var body = $("#stockBody");
  if (!list.length) {
    var msg = chem.drugs.length === 0
      ? "系统中还没有药品，可在上方下载 Excel 模板导入入库"
      : "没有匹配的药品";
    body.innerHTML = '<tr><td colspan="4" class="empty">' + msg + "</td></tr>";
    return;
  }

  body.innerHTML = list.map(function (d) {
    var low = isLowStock(d);
    var cls = low ? "stock-low" : "stock-num";
    var rowCls = low ? ' class="row-low"' : "";
    // 设置提醒阈值为管理员功能：仅管理员可见 ⚙ 入口
    var gear = isAdmin()
      ? '<button type="button" class="icon-btn" data-act="th" data-id="' + d.id +
        '" title="设置「' + escapeHtml(d.name) + '」的提醒阈值">⚙</button>'
      : "";
    return "<tr" + rowCls + ">" +
      "<td>" + escapeHtml(d.name) + "</td>" +
      "<td>" + escapeHtml(d.unit) + "</td>" +
      '<td><span class="' + cls + '">' + formatQty(d.stock) + "</span></td>" +
      '<td class="col-act">' + gear + "</td>" +
      "</tr>";
  }).join("");
}

// ------------------------------------------------------------
// 在线设置低库存提醒阈值（⚙ 图标入口）
// ------------------------------------------------------------
var thDrugId = null;   // 当前正在设置阈值的药品 id

function ensureThModal() {
  if ($("#thModal")) return;
  var m = document.createElement("div");
  m.id = "thModal";
  m.className = "modal hidden";
  m.innerHTML =
    '<div class="modal-card">' +
      '<button type="button" class="modal-close" id="thClose" title="关闭">×</button>' +
      "<h3>设置提醒阈值</h3>" +
      '<p class="modal-drug" id="thDrugName"></p>' +
      '<p class="modal-tip">剩余库存低于该数值时会标红并排在最前面；<br>留空表示不提醒。</p>' +
      '<input type="number" id="thInput" class="input" min="0" step="any" ' +
        'placeholder="留空表示不提醒">' +
      '<div class="modal-err hidden" id="thErr"></div>' +
      '<div class="modal-actions">' +
        '<button type="button" class="btn btn-ghost" id="thCancel">取消</button>' +
        '<button type="button" class="btn btn-primary" id="thSave">保存</button>' +
      "</div>" +
    "</div>";
  document.body.appendChild(m);

  $("#thClose").addEventListener("click", closeThModal);
  $("#thCancel").addEventListener("click", closeThModal);
  $("#thSave").addEventListener("click", saveThreshold);
  $("#thInput").addEventListener("input", function () {
    $("#thErr").classList.add("hidden");
  });
  $("#thInput").addEventListener("keydown", function (e) {
    if (e.key === "Enter") saveThreshold();
  });
  // 点遮罩空白处关闭
  m.addEventListener("click", function (e) {
    if (e.target === m) closeThModal();
  });
}

function openThModal(drugId) {
  var drug = chem.drugs.find(function (d) { return d.id === drugId; });
  if (!drug) return;
  ensureThModal();
  thDrugId = drugId;
  $("#thDrugName").textContent = drug.name + "（当前剩余 " + formatQty(drug.stock) + " " + drug.unit + "）";
  $("#thInput").value = drug.low_threshold == null ? "" : drug.low_threshold;
  $("#thErr").classList.add("hidden");
  $("#thModal").classList.remove("hidden");
  $("#thInput").focus();
}

function closeThModal() {
  var m = $("#thModal");
  if (m) m.classList.add("hidden");
  thDrugId = null;
}

async function saveThreshold() {
  if (thDrugId == null) return;
  var raw = $("#thInput").value.trim();
  var value;
  if (raw === "") {
    value = null;  // 不提醒
  } else {
    value = parseFloat(raw);
    if (isNaN(value) || value <= 0) {
      $("#thErr").textContent = "提醒阈值必须是大于 0 的数字，或留空表示不提醒";
      $("#thErr").classList.remove("hidden");
      return;
    }
  }

  var btn = $("#thSave");
  btn.disabled = true;
  try {
    var data = await api("/api/drugs/" + thDrugId + "/threshold", {
      method: "POST",
      body: JSON.stringify({ low_threshold: value }),
    });
    chem.drugs = data.drugs;
    closeThModal();
    renderStock();
    toast(value == null ? "已取消该药品的提醒" : "提醒阈值已设为 " + formatQty(value), "success");
  } catch (err) {
    $("#thErr").textContent = err.message;
    $("#thErr").classList.remove("hidden");
  } finally {
    btn.disabled = false;
  }
}

function renderImportErrors(data) {
  var box = $("#importResult");
  var rows = (data && data.errors) || [];
  var html = '<div class="import-error-head">' +
    escapeHtml(data && data.error ? data.error : "导入失败") + "</div>";
  if (rows.length) {
    html += '<ul class="import-error-list">' + rows.map(function (e) {
      var where = e.row ? ("第 " + e.row + " 行") : "文件";
      var who = e.name ? "（" + escapeHtml(e.name) + "）" : "";
      var msgs = (e.messages || []).map(escapeHtml).join("；");
      return "<li><strong>" + where + "</strong>" + who + "：" + msgs + "</li>";
    }).join("") + "</ul>";
  }
  box.innerHTML = html;
  box.classList.remove("hidden");
}

async function submitImport() {
  var fileInput = $("#importFile");
  var file = fileInput.files && fileInput.files[0];
  if (!file) return toast("请先选择填好的 .xlsx 文件", "error");

  var form = new FormData();
  form.append("file", file);
  form.append("operator", currentOperator());

  var btn = $("#importBtn");
  btn.disabled = true;
  btn.textContent = "导入中…";
  try {
    var _pre = (typeof base_url === "string") ? base_url : "";
    var resp = await fetch(_pre + "/api/import/excel", { method: "POST", body: form });
    var data = await resp.json().catch(function () { return {}; });
    if (!resp.ok) {
      renderImportErrors(data);
      toast(data.error || "导入失败", "error");
      return;
    }
    chem.drugs = data.drugs || [];
    $("#importResult").classList.add("hidden");
    $("#importResult").innerHTML = "";
    fileInput.value = "";
    renderStock();
    var msg = "Excel 导入成功，共入库 " + data.imported + " 条";
    if (data.created || data.appended) {
      msg += "（新药品 " + (data.created || 0) + " 种、已有药品 "
        + (data.appended || 0) + " 种）";
    }
    toast(msg, "success");
  } catch (err) {
    toast(err.message || "导入失败，请检查网络后重试", "error");
  } finally {
    btn.textContent = "开始导入";
    btn.disabled = !fileInput.files || !fileInput.files.length;
  }
}

async function init() {
  $("#stockSearch").addEventListener("input", renderStock);
  // 库存行内 ⚙ 图标：打开阈值设置弹窗（事件委托）
  $("#stockBody").addEventListener("click", function (e) {
    var btn = e.target.closest(".icon-btn");
    if (btn) openThModal(Number(btn.getAttribute("data-id")));
  });
  $("#importFile").addEventListener("change", function (e) {
    $("#importBtn").disabled = !e.target.files.length;
    $("#importResult").classList.add("hidden");
  });
  $("#importBtn").addEventListener("click", submitImport);

  try {
    await loadDrugs();
    // 系统为空（第一次使用）时自动展开导入区，平时保持折叠只显示标题行
    if (chem.drugs.length === 0) $("#importDetails").open = true;
    renderStock();
  } catch (err) {
    $("#stockBody").innerHTML =
      '<tr><td colspan="3" class="empty">库存加载失败：' + escapeHtml(err.message) + "</td></tr>";
  }
}

init();

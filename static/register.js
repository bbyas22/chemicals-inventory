/* ============================================================
   登记页面：手动登记 + AI 辅助登记
   AI 预登记：一句话可拆出多条，逐条标红 → 未修正禁止入库 →
             可单条删除 → 全部有效后批量原子入库
   ============================================================ */

var pickerMode = localStorage.getItem("chem_picker_mode") || "browse";

// ------------------------------------------------------------
// 药品选择控件
// ------------------------------------------------------------
function renderDrugPickers() {
  var list = sortedDrugs();

  $("#drugDataList").innerHTML = list
    .map(function (d) {
      return '<option value="' + d.name + '">' + d.unit + " · 剩余 " + formatQty(d.stock) + "</option>";
    })
    .join("");

  var select = $("#manualDrugSelect");
  var prev = select.value;
  select.innerHTML = list
    .map(function (d) {
      return '<option value="' + d.id + '">' + d.name + "（剩余 " + formatQty(d.stock) + " " + d.unit + "）</option>";
    })
    .join("");
  if (prev && list.some(function (d) { return String(d.id) === prev; })) {
    select.value = prev;
  }
}

function applyPickerMode() {
  var browse = pickerMode === "browse";
  $("#manualDrugSelect").classList.toggle("hidden", !browse);
  $("#manualDrugInput").classList.toggle("hidden", browse);
  $$('input[name="pickerMode"]').forEach(function (r) {
    r.checked = r.value === pickerMode;
  });
  localStorage.setItem("chem_picker_mode", pickerMode);
  updateManualHint();
}

// ------------------------------------------------------------
// 手动登记
// ------------------------------------------------------------
function getManualType() {
  return document.querySelector('input[name="changeType"]:checked').value;
}

function getManualDrug() {
  if (pickerMode === "browse") {
    var id = Number($("#manualDrugSelect").value);
    return chem.drugs.find(function (d) { return d.id === id; }) || null;
  }
  return findDrugByName($("#manualDrugInput").value);
}

function updateManualHint() {
  var drug = getManualDrug();
  var type = getManualType();
  var qty = parseFloat($("#manualQty").value);
  var hint = $("#manualStockHint");
  var newUnitField = $("#manualNewUnitField");

  if (pickerMode === "search" && !drug && $("#manualDrugInput").value.trim()) {
    if (type === "in") {
      newUnitField.classList.remove("hidden");
      hint.classList.remove("warn");
      hint.textContent = "未找到该药品，本次入库将自动建立药品档案。";
    } else {
      newUnitField.classList.add("hidden");
      hint.classList.add("warn");
      hint.textContent = "库中没有该药品，无法登记使用；请先入库登记。";
    }
    $("#manualUnit").textContent = "—";
    return;
  }

  newUnitField.classList.add("hidden");
  if (!drug) {
    hint.classList.remove("warn");
    hint.textContent = "请选择药品以查看当前剩余量";
    $("#manualUnit").textContent = "—";
    return;
  }

  $("#manualUnit").textContent = drug.unit;
  hint.classList.remove("warn");
  var text = "当前剩余：" + formatQty(drug.stock) + " " + drug.unit;
  if (qty > 0) {
    if (type === "use") {
      text += "；登记后剩余：" + formatQty(drug.stock - qty) + " " + drug.unit;
      if (qty > drug.stock) {
        text += "（库存不足！）";
        hint.classList.add("warn");
      }
    } else {
      text += "；登记后剩余：" + formatQty(drug.stock + qty) + " " + drug.unit;
    }
  }
  hint.textContent = text;
}

async function submitManual(e) {
  e.preventDefault();
  var type = getManualType();
  var qty = parseFloat($("#manualQty").value);
  if (!qty || qty <= 0) return toast("请输入大于 0 的数量", "error");

  var payload = {
    change_type: type,
    quantity: qty,
    note: $("#manualNote").value.trim(),
    operator: currentOperator(),
  };

  if (pickerMode === "browse") {
    payload.drug_id = Number($("#manualDrugSelect").value);
  } else {
    var name = $("#manualDrugInput").value.trim();
    if (!name) return toast("请填写药品名称", "error");
    payload.drug_name = name;
    if (!findDrugByName(name)) {
      if (type !== "in") return toast("库中没有该药品，无法登记使用", "error");
      payload.unit = $("#manualNewUnit").value;
    }
  }

  var btn = $("#manualSubmit");
  btn.disabled = true;
  try {
    var data = await api("/api/records", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    chem.drugs = data.drugs;
    renderDrugPickers();
    $("#manualQty").value = "";
    $("#manualNote").value = "";
    updateManualHint();
    toast("登记成功，库存已更新", "success");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    btn.disabled = false;
  }
}

// ------------------------------------------------------------
// AI 辅助登记（多条）
// ------------------------------------------------------------
var aiItems = [];   // [{uid, change_type, drug_name, quantity, unit, note}]
var aiUidSeq = 0;

var UNIT_OPTIONS = ["克", "毫克", "毫升", "升", "瓶", "个", "盒", "包"];

async function aiParse() {
  var text = $("#aiText").value.trim();
  if (!text) return toast("请先输入药品使用描述", "error");

  var btn = $("#aiParseBtn");
  btn.disabled = true;
  btn.textContent = "AI 解析中…";
  try {
    var data = await api("/api/ai/parse", {
      method: "POST",
      body: JSON.stringify({ text: text }),
    });
    fillAiPreview(data);
  } catch (err) {
    // 含“连续 3 次无法解析 / AI 识别失败”等后端信息
    toast(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "AI 解析";
  }
}

/**
 * 填充 AI 解析结果。
 * 标准形态：{items: [...]}；
 * 兼容单条对象（测试/旧调用）：自动包装成长度 1 的数组。
 */
function fillAiPreview(payload) {
  var list = Array.isArray(payload.items) ? payload.items : [payload];
  aiItems = list.map(function (d) {
    return {
      uid: ++aiUidSeq,
      change_type: d.change_type === "in" ? "in" : "use",
      drug_name: d.drug_name || "",
      quantity: d.quantity == null ? "" : d.quantity,
      unit: d.unit || "克",
      note: d.note || "",
    };
  });
  renderAiItems();
  $("#aiPreview").classList.remove("hidden");
}

function unitOptionsHtml(selected) {
  // 首个空选项用于新药建档时的“请选择单位”校验；
  // AI 识别出的非常用单位（如“袋”）动态追加，保证可选中
  var opts = UNIT_OPTIONS.slice();
  if (selected && opts.indexOf(selected) === -1) opts.push(selected);
  return '<option value=""' + (!selected ? " selected" : "") + '>单位</option>' +
    opts.map(function (u) {
      return '<option value="' + u + '"' + (u === selected ? " selected" : "") + ">" + u + "</option>";
    }).join("");
}

/**
 * 渲染全部 AI 条目。
 * 正常条目只占一行：[入库/使用] [药品名] [数量] [单位] [余N] [×]；
 * 存在问题的条目自动展开详细修改区（错误原因、备注、库存明细）。
 */
function renderAiItems() {
  var container = $("#aiItems");
  container.innerHTML = aiItems.map(function (item) {
    return (
      '<div class="ai-item" data-uid="' + item.uid + '">' +
        '<div class="ai-item-row">' +
          '<div class="seg-mini">' +
            '<label class="seg-opt seg-in">' +
              '<input type="radio" name="aiType_' + item.uid + '" value="in"' +
                (item.change_type === "in" ? " checked" : "") + ">" +
              '<span class="lbl-full">入库</span><span class="lbl-mini">入</span>' +
            "</label>" +
            '<label class="seg-opt seg-use">' +
              '<input type="radio" name="aiType_' + item.uid + '" value="use"' +
                (item.change_type === "use" ? " checked" : "") + ">" +
              '<span class="lbl-full">使用</span><span class="lbl-mini">用</span>' +
            "</label>" +
          "</div>" +
          '<input type="text" class="input ai-cell js-drug" list="drugDataList" ' +
            'autocomplete="off" placeholder="药品名称" value="' +
            escapeHtml(item.drug_name) + '">' +
          '<input type="number" class="input ai-cell js-qty" min="0" step="any" ' +
            'inputmode="decimal" placeholder="数量" value="' +
            (item.quantity === "" ? "" : item.quantity) + '">' +
          '<select class="input ai-cell js-unit" title="单位">' +
            unitOptionsHtml(item.unit) + "</select>" +
          '<span class="ai-stock-brief js-brief"></span>' +
          '<button type="button" class="ai-item-del" data-act="del" ' +
            'title="删除此条" aria-label="删除此条">×</button>' +
        "</div>" +
        '<div class="ai-item-detail">' +
          '<div class="field-msg js-drug-msg"></div>' +
          '<div class="field-msg js-qty-msg"></div>' +
          '<div class="field-msg js-unit-msg"></div>' +
          '<input type="text" class="input js-note" maxlength="100" ' +
            'placeholder="备注（可选）" value="' + escapeHtml(item.note) + '">' +
          '<div class="stock-hint js-hint"></div>' +
        "</div>" +
      "</div>"
    );
  }).join("");

  // 事件绑定（卡片渲染后一次性委托绑定）
  $$("#aiItems .ai-item").forEach(function (card) {
    var item = findItemByCard(card);

    card.querySelector(".js-drug").addEventListener("input", function (e) {
      item.drug_name = e.target.value;
      refreshAiSummary();
    });
    card.querySelector(".js-qty").addEventListener("input", function (e) {
      item.quantity = e.target.value;
      refreshAiSummary();
    });
    card.querySelector(".js-unit").addEventListener("change", function (e) {
      item.unit = e.target.value;
    });
    card.querySelector(".js-note").addEventListener("input", function (e) {
      item.note = e.target.value;
    });
    $$('input[name="aiType_' + item.uid + '"]').forEach(function (r) {
      r.addEventListener("change", function () {
        item.change_type = r.value;
        refreshAiSummary();
      });
    });
    card.querySelector('[data-act="del"]').addEventListener("click", function () {
      deleteAiItem(item.uid);
    });

    validateAiCard(card, item);
  });

  refreshAiSummary();
}

function findItemByCard(card) {
  var uid = Number(card.getAttribute("data-uid"));
  return aiItems.find(function (it) { return it.uid === uid; });
}

/** 直接给紧凑行中的输入框标红/取消标红，并写入展开区的错误说明 */
function setFieldInvalid(inputEl, msgEl, message) {
  if (message) {
    inputEl.classList.add("invalid");
    msgEl.textContent = message;
  } else {
    inputEl.classList.remove("invalid");
    msgEl.textContent = "";
  }
}

/**
 * 实时复验单条 AI 记录：
 * 正常条目保持单行折叠；存在问题时整条标红并展开详细修改区。
 * 入库类允许库外药品（提交后自动新建档案）；使用类必须在库中存在。
 * 返回该条是否可入库。
 */
function validateAiCard(card, item) {
  var name = (item.drug_name || "").trim();
  var qty = parseFloat(item.quantity);
  var drug = findDrugByName(name);
  var isNew = !drug && item.change_type === "in";   // 入库新药：将自动建档

  var drugInput = card.querySelector(".js-drug");
  var qtyInput = card.querySelector(".js-qty");

  // 药品：使用类必须在库中；入库类允许新名称（将建档）
  setFieldInvalid(
    drugInput,
    card.querySelector(".js-drug-msg"),
    (drug || (isNew && name))
      ? ""
      : (item.change_type === "in"
          ? "请填写药品名称"
          : "药品库中不存在「" + (name || "（空）") + "」，请修改为库中药品")
  );

  // 数量有效性 + 使用量是否超出库存（新药无库存概念）
  var qtyMsg = "";
  if (!qty || qty <= 0) {
    qtyMsg = "请填写大于 0 的有效数量";
  } else if (drug && item.change_type === "use" && qty > drug.stock) {
    qtyMsg = "使用量超出库存，当前仅剩 " + formatQty(drug.stock) + " " + drug.unit;
  }
  setFieldInvalid(qtyInput, card.querySelector(".js-qty-msg"), qtyMsg);

  // 单位：库内药品锁定为库存单位；入库新药可选/确认单位（必填）
  var unitSel = card.querySelector(".js-unit");
  var unitMsg = "";
  if (drug) {
    unitSel.value = drug.unit;
    item.unit = drug.unit;
    unitSel.disabled = true;
  } else {
    unitSel.disabled = false;
    item.unit = unitSel.value || "";
    if (isNew && !item.unit) {
      unitMsg = "新药品入库需要选择计量单位";
    }
  }
  setFieldInvalid(unitSel, card.querySelector(".js-unit-msg"), unitMsg);

  // 紧凑行右侧的淡色剩余量
  var brief = card.querySelector(".js-brief");
  brief.textContent = drug
    ? "余 " + formatQty(drug.stock) + drug.unit
    : (isNew && name ? "新药" : "");

  // 展开区库存明细
  var hint = card.querySelector(".js-hint");
  if (!drug) {
    if (isNew && name) {
      hint.classList.remove("warn");
      hint.textContent = "库中没有「" + name + "」，提交后将按所选单位（" +
        (item.unit || "未选") + "）新建档案并入库。";
    } else {
      hint.classList.add("warn");
      hint.textContent = "库中没有该药品，请修改药品名称为库中药品。";
    }
  } else {
    hint.classList.remove("warn");
    var text = "当前剩余：" + formatQty(drug.stock) + " " + drug.unit;
    if (qty > 0) {
      if (item.change_type === "use") {
        text += "；登记后剩余：" + formatQty(drug.stock - qty) + " " + drug.unit;
        if (qty > drug.stock) {
          text += "（库存不足！）";
          hint.classList.add("warn");
        }
      } else {
        text += "；登记后剩余：" + formatQty(drug.stock + qty) + " " + drug.unit;
      }
    }
    hint.textContent = text;
  }

  var ok = drug
    ? !qtyMsg
    : !!(isNew && name && item.unit && !qtyMsg && !unitMsg);
  card.classList.toggle("has-error", !ok);
  return ok;
}

/** 根据各卡片当前状态刷新阻断横幅与提交按钮 */
function refreshAiSummary() {
  var invalidCount = 0;
  $$("#aiItems .ai-item").forEach(function (card) {
    var item = findItemByCard(card);
    if (!validateAiCard(card, item)) invalidCount += 1;
  });

  var total = aiItems.length;
  $("#aiBlockBanner").classList.toggle("hidden", invalidCount === 0);
  $("#aiSubmit").disabled = invalidCount > 0 || total === 0;
  $("#aiValidCount").textContent = total;
  $("#aiPreviewTitle").textContent =
    "解析结果（共 " + total + " 条），请逐条核对后可直接修改";
}

/** 删除单条；删完最后一条时整个预览区收起 */
function deleteAiItem(uid) {
  aiItems = aiItems.filter(function (it) { return it.uid !== uid; });
  if (!aiItems.length) {
    clearAiPreview();
    toast("已删除该条 AI 识别数据");
    return;
  }
  renderAiItems();
  toast("已删除该条 AI 识别数据");
}

function clearAiPreview() {
  aiItems = [];
  $("#aiItems").innerHTML = "";
  $("#aiPreview").classList.add("hidden");
  $("#aiText").value = "";
}

/** 全部条目批量原子入库 */
async function submitAiBatch() {
  // 提交前整体复验一次
  var invalid = [];
  var payloadItems = [];
  $$("#aiItems .ai-item").forEach(function (card) {
    var item = findItemByCard(card);
    var ok = validateAiCard(card, item);
    if (!ok) {
      invalid.push(item);
      return;
    }
    var drug = findDrugByName(item.drug_name);
    if (drug) {
      payloadItems.push({
        change_type: item.change_type,
        drug_id: drug.id,
        quantity: parseFloat(item.quantity),
        note: (item.note || "").trim(),
      });
    } else {
      // 入库新药：传名称与单位，服务端自动建档
      payloadItems.push({
        change_type: item.change_type,
        drug_name: (item.drug_name || "").trim(),
        unit: item.unit || "",
        quantity: parseFloat(item.quantity),
        note: (item.note || "").trim(),
      });
    }
  });

  if (invalid.length || payloadItems.length !== aiItems.length) {
    toast("标红字段未修正，无法入库", "error");
    refreshAiSummary();
    return;
  }

  var btn = $("#aiSubmit");
  btn.disabled = true;
  try {
    var data = await api("/api/records/batch", {
      method: "POST",
      body: JSON.stringify({ items: payloadItems, operator: currentOperator() }),
    });
    chem.drugs = data.drugs;
    renderDrugPickers();
    var n = data.records.length;
    clearAiPreview();
    toast(n + " 条 AI 登记全部成功，库存已更新", "success");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    btn.disabled = false;
    refreshAiSummary();
  }
}

// ------------------------------------------------------------
// 初始化
// ------------------------------------------------------------
function bindEvents() {
  // Tabs
  $$(".tab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      $$(".tab").forEach(function (t) { t.classList.remove("active"); });
      $$(".tab-panel").forEach(function (p) { p.classList.remove("active"); });
      tab.classList.add("active");
      $("#panel-" + tab.dataset.tab).classList.add("active");
    });
  });

  // 偏好
  $$('input[name="pickerMode"]').forEach(function (r) {
    r.addEventListener("change", function () {
      pickerMode = r.value;
      applyPickerMode();
    });
  });

  // 手动登记
  $("#manualForm").addEventListener("submit", submitManual);
  $("#manualDrugSelect").addEventListener("change", updateManualHint);
  $("#manualDrugInput").addEventListener("input", updateManualHint);
  $("#manualQty").addEventListener("input", updateManualHint);
  $$('input[name="changeType"]').forEach(function (r) {
    r.addEventListener("change", updateManualHint);
  });

  // AI 辅助登记
  $("#aiParseBtn").addEventListener("click", aiParse);
  $("#aiSubmit").addEventListener("click", submitAiBatch);
  $("#aiClearBtn").addEventListener("click", function () {
    clearAiPreview();
    toast("已清空 AI 识别结果");
  });
}

async function init() {
  bindEvents();
  applyPickerMode();
  try {
    await loadDrugs();
    renderDrugPickers();
    updateManualHint();
    // 系统为空时展示 Excel 导入引导
    var guide = $("#emptyGuide");
    if (guide) guide.classList.toggle("hidden", chem.drugs.length > 0);
  } catch (err) {
    toast(err.message, "error");
  }
}

init();

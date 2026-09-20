/* ============================================================
   公共脚本：导航、通用工具、药品库存数据共享
   ============================================================ */

var $ = function (sel) { return document.querySelector(sel); };
var $$ = function (sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); };

// 全局状态：各页面共用的药品库存数据
var chem = {
  drugs: [],  // [{id,name,unit,stock}]
};

// 中文按拼音首字母排序（现代浏览器 Intl 使用拼音排序），不支持时回退
var zhCollator =
  typeof Intl !== "undefined" && Intl.Collator
    ? new Intl.Collator("zh-Hans-CN", { sensitivity: "accent" })
    : { compare: function (a, b) { return a.localeCompare(b, "zh-CN"); } };

// 是否低于库存提醒值（low_threshold 为空表示该药品不提醒）
function isLowStock(d) {
  return d.low_threshold != null && d.stock < d.low_threshold;
}

// 排序：低于提醒值的药品排最前面（组内再按名称），其余按名称
function sortedDrugs() {
  return chem.drugs.slice().sort(function (a, b) {
    var la = isLowStock(a) ? 0 : 1;
    var lb = isLowStock(b) ? 0 : 1;
    if (la !== lb) return la - lb;
    return zhCollator.compare(a.name, b.name);
  });
}

function findDrugByName(name) {
  var key = (name || "").trim();
  return chem.drugs.find(function (d) { return d.name === key; });
}

function formatQty(n) {
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 10000) / 10000);
}

// ------------------------------------------------------------
// 部署前缀 base_url：由各页面 head 内联脚本自动探测并定义（见 base.html）。
// 取地址栏路径第一段，若非应用顶层路由（register/inventory/records/api/static）
// 则视为反代前缀（如 /lab、/chemicals 等任意路径），否则为空。
// 页面内所有请求路径统一用 base_url + "/..." 拼接。
// ------------------------------------------------------------

// 页面内根绝对链接（/register、/api/... 等）统一改写为 base_url 前缀
(function rewriteLinks() {
  var pre = (typeof base_url === "string") ? base_url : "";
  if (!pre) return;
  document.querySelectorAll('a[href^="/"]').forEach(function (a) {
    a.setAttribute("href", pre + a.getAttribute("href"));
  });
})();
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function toast(msg, type) {
  var el = $("#toast");
  el.textContent = msg;
  el.className = "toast " + (type || "");
  clearTimeout(toast._t);
  toast._t = setTimeout(function () { el.classList.add("hidden"); }, 3000);
}

async function api(url, options) {
  // 所有请求路径统一拼接部署前缀 base_url（head 内联脚本探测）
  var pre = (typeof base_url === "string") ? base_url : "";
  if (url.charAt(0) !== "/") url = "/" + url;
  var resp = await fetch(pre + url, Object.assign(
    { headers: { "Content-Type": "application/json" } },
    options || {}
  ));
  var data = await resp.json().catch(function () { return {}; });
  if (!resp.ok) throw new Error(data.error || ("请求失败（" + resp.status + "）"));
  return data;
}

async function loadDrugs() {
  chem.drugs = await api("/api/drugs");
  return chem.drugs;
}

// 导航高亮
(function initNav() {
  var page = document.body.getAttribute("data-page");
  $$("[data-nav]").forEach(function (a) {
    if (a.getAttribute("data-nav") === page) a.classList.add("active");
  });
})();

// ------------------------------------------------------------
// 首次使用强制登记：真实姓名保存在浏览器本地，之后每次登记自动作为操作人；
// 首次使用需输入管理密码（服务端校验），之后不再需要
// ------------------------------------------------------------
var OPERATOR_KEY = "chem_user_name";
var ROLE_KEY = "chem_user_role";

function currentOperator() {
  return localStorage.getItem(OPERATOR_KEY) || "";
}

// 是否管理员：首次登记输入管理员密码后保存的角色标记
function isAdmin() {
  return localStorage.getItem(ROLE_KEY) === "admin";
}

function initUserGate() {
  if (currentOperator()) return;

  var gate = document.createElement("div");
  gate.id = "userGate";
  gate.className = "user-gate";
  gate.innerHTML =
    '<form class="user-gate-card" id="userGateForm">' +
      "<h2>首次使用登记</h2>" +
      '<p class="user-gate-tip">请登记您的真实姓名，之后的每次入库 / 使用登记都会把您记录为操作人。</p>' +
      '<label class="user-gate-field">真实姓名' +
        '<input type="text" id="gateName" class="input" maxlength="20" ' +
          'placeholder="请输入真实姓名" autocomplete="name">' +
      "</label>" +
      '<label class="user-gate-field">管理密码' +
        '<input type="password" id="gatePassword" class="input" ' +
          'placeholder="仅首次使用需要输入" autocomplete="off">' +
      "</label>" +
      '<div class="user-gate-error hidden" id="gateError"></div>' +
      '<button type="submit" class="btn btn-primary btn-block" id="gateSubmit">确认登记</button>' +
    "</form>";
  document.body.appendChild(gate);

  gate.addEventListener("submit", async function (e) {
    e.preventDefault();
    var name = $("#gateName").value.trim();
    var password = $("#gatePassword").value;
    var errBox = $("#gateError");
    errBox.classList.add("hidden");

    if (!name) {
      errBox.textContent = "请填写真实姓名";
      errBox.classList.remove("hidden");
      return;
    }
    if (!password) {
      errBox.textContent = "请输入管理密码";
      errBox.classList.remove("hidden");
      return;
    }

    var btn = $("#gateSubmit");
    btn.disabled = true;
    try {
      var data = await api("/api/user/verify", {
        method: "POST",
        body: JSON.stringify({ name: name, password: password }),
      });
      localStorage.setItem(OPERATOR_KEY, name);
      localStorage.setItem(ROLE_KEY, data.role || "user");
      gate.remove();
      toast(
        data.role === "admin"
          ? "登记成功，欢迎管理员 " + name
          : "登记成功，欢迎 " + name,
        "success"
      );
    } catch (err) {
      errBox.textContent = err.message;
      errBox.classList.remove("hidden");
      btn.disabled = false;
    }
  });

  $("#gateName").focus();
}

initUserGate();

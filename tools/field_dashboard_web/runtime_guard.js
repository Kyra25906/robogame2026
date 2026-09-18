/*
 * 「真伪与实例核查」卡片。
 *
 * 解决两个现场最容易造成事故、又最难自己发现的误判：
 *   1. **真假**：`robot_bridge` 的 `mock_mode` 默认是 True。只带共用层启动就是
 *      假数据——安全总览照样显示「通信正常」，而车上根本没接固件。
 *   2. **双实例**：控制台只知道自己 spawn 的进程，对「有人先在 SSH 里起了
 *      hardware.launch.py」无感。起了第二个 bridge 的后果不是一句报错，而是
 *      `runtime_source_guard` 关掉整个 launch，或两个进程抢同一个串口。
 *
 * 判据全部由后端给出（`tools/field_runtime_guard.py`，与 launch 里的 source guard
 * 共用同一份分类函数与话题清单）。本文件只负责**如实显示**，不自己下判断——
 * 否则页面会把「后端判为真车」和「页面以为真车」混在一起，那正是要避免的事。
 *
 * 与其它面板一样：整个卡片由本文件在加载时插入 DOM，index.html 只需一行 script。
 * 渲染入口 `renderRuntimeGuard(snapshot)` 由 app.js 在每次刷新后调用（必须在
 * app.js 重建「进程」列表之后，否则它重建的按钮会把禁用状态冲掉）。
 */
(function () {
  "use strict";

  const LEVEL_TEXT = { block: "阻断", warn: "警告", info: "说明", ok: "正常" };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (ch) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
    ));
  }

  /** 纯函数：把后端核查结论变成页面要显示的一切（便于 node 单测）。 */
  function runtimeGuardView(check) {
    if (!check || typeof check !== "object") {
      return {
        available: false,
        tone: "unknown",
        headline: "核查未提供：网页服务版本过旧（snapshot 里没有 runtime_check）",
        subtitle: "",
        findingsHtml: "",
        topicsHtml: "",
        instancesText: "",
        scanText: "",
        blockedStarts: [],
        blockedText: "",
      };
    }
    const tone = check.verdict === "block" ? "bad"
      : check.verdict === "warn" ? "unknown" : "ok";
    const source = check.source || {};
    const subtitle = `来源判定：${source.label || "未知"}`
      + `｜期望来源 ${check.expected_source || "field"}`
      + `｜样本 ${source.sample_count || 0} 条`
      + (source.sample ? `｜最近 detail=${JSON.stringify(source.sample)}` : "");
    const findings = Array.isArray(check.findings) ? check.findings : [];
    const findingsHtml = findings.map((item) => {
      const cls = item.level === "block" ? "bad" : item.level === "warn" ? "unknown" : "ok";
      return `<div class="chain-row ${cls}"><b>${escapeHtml(LEVEL_TEXT[item.level] || item.level)}</b>`
        + `<span>${escapeHtml(item.message)}</span>`
        + `<em>${escapeHtml(item.code)}｜${escapeHtml(item.evidence)}</em></div>`;
    }).join("");
    const topics = Array.isArray(check.topics) ? check.topics : [];
    const topicsHtml = topics.map((row) => {
      const cls = row.level === "block" ? "bad" : row.level === "warn" ? "unknown"
        : row.level === "ok" ? "ok" : "";
      const names = row.publishers && row.publishers.length
        ? row.publishers.join(", ") : "（无发布者）";
      return `<div class="chain-row ${cls}"><b>${row.count}</b>`
        + `<span>${escapeHtml(row.topic)}${row.critical ? "（关键话题）" : ""}</span>`
        + `<em>${escapeHtml(names)}</em></div>`;
    }).join("");
    const instances = check.instances || {};
    const describe = (list) => (list || []).map(
      (item) => `pid ${item.pid}｜${item.node || "?"}｜${item.args}`,
    );
    const managed = describe(instances.managed);
    const external = describe(instances.external);
    const instancesText = "本网页启动的已知节点：\n"
      + (managed.length ? managed.join("\n") : "（无）")
      + "\n\n网页之外在跑的已知节点（真正的盲区）：\n"
      + (external.length ? external.join("\n") : "（未发现）");
    const scanText = instances.scan_available === false
      ? `进程表：不可用——${check.scan_note || "本机不提供"}（**不能**据此认为没有第二个实例）`
      : `进程表：已扫描（${(instances.external || []).length} 个网页之外的已知节点）`;
    const blockedStarts = Array.isArray(check.blocked_starts) ? check.blocked_starts : [];
    const blockedText = blockedStarts.length
      ? `现在被禁止启动的网页进程：${blockedStarts.join("、")} —— 起了就是第二个实例，`
        + "按钮会给出拒绝理由（证据含 PID 与命令行）。"
      : "现在没有进程被禁止启动。";
    return {
      available: true, tone, headline: check.headline || "(无结论)",
      subtitle, findingsHtml, topicsHtml, instancesText, scanText,
      blockedStarts, blockedText,
    };
  }

  let bound = false;

  //: 颜色纪律：红 = 真的被拦住（起了就是第二个实例）；灰 = 需要人注意但允许继续。
  //: 把警告也画成红色，会让「有 mock 混进来」和「已经双实例」看起来一样严重，
  //: 结果是人开始忽略红色的东西——那比不显示更糟。
  const TONE_CLASS = { ok: "ok", bad: "bad", unknown: "unknown" };

  function buildCard() {
    const section = el("section", "card wide");
    section.appendChild(el("h2", null, "真伪与实例核查（真车 / mock、有没有第二个实例）"));
    const live = el("div", "route-live unknown", "读取中…");
    live.id = "runtimeLive";
    section.appendChild(live);
    section.appendChild(el("p", "hint",
      "判据与 launch 里的 runtime_source_guard 同源：/robot/status 的 detail 决定真假，"
      + "关键话题的发布者数量决定有没有双实例，进程表里「不是本网页启动」的已知节点另算一路。"
      + "真伪说「真车」也不等于车上烧的是仓库里这份固件。"));
    const findings = el("div", "chain-table");
    findings.id = "runtimeFindings";
    section.appendChild(findings);
    section.appendChild(el("h3", null, "关键话题发布者（已忽略本面板自身 field_dashboard）"));
    const topics = el("div", "chain-table");
    topics.id = "runtimeTopics";
    section.appendChild(topics);
    const instances = el("pre", "hint");
    instances.id = "runtimeInstances";
    section.appendChild(instances);
    const scan = el("p", "hint");
    scan.id = "runtimeScan";
    section.appendChild(scan);
    const blocked = el("p", "hint");
    blocked.id = "runtimeBlocked";
    section.appendChild(blocked);
    return section;
  }

  /** 把「被禁止启动」落到按钮上。必须在 app.js 重建进程列表之后调用。 */
  function applyBlockedStarts(blockedStarts) {
    const pressed = new Set(blockedStarts || []);
    document.querySelectorAll("[data-start]").forEach((button) => {
      const name = button.dataset.start;
      if (!pressed.has(name)) return;
      button.disabled = true;
      button.title = "已有另一个实例在跑：先停掉它（网页「真伪与实例核查」有 PID 与命令行）";
    });
    document.querySelectorAll("[data-group]").forEach((button) => {
      // 「启动底盘链路」一次起 bridge + localization + motion：其中任何一个被禁，
      // 整组按钮都该拦下来——半启动状态比不启动更难排查。
      const group = ["bridge", "localization", "motion"].some((n) => pressed.has(n));
      if (group) {
        button.disabled = true;
        button.title = "该组里有节点已有外部实例在跑：先停掉再启动整组";
      }
    });
  }

  function renderRuntimeGuard(snapshot) {
    const live = document.querySelector("#runtimeLive");
    if (!live) return;  // 卡片还没插入（DOMContentLoaded 之前）
    const view = runtimeGuardView(snapshot && snapshot.runtime_check);
    live.textContent = view.headline + (view.subtitle ? "\n" + view.subtitle : "");
    live.className = "route-live " + (TONE_CLASS[view.tone] || "unknown");
    const findings = document.querySelector("#runtimeFindings");
    if (findings) findings.innerHTML = view.findingsHtml;
    const topics = document.querySelector("#runtimeTopics");
    if (topics) topics.innerHTML = view.topicsHtml;
    const instances = document.querySelector("#runtimeInstances");
    if (instances) instances.textContent = view.instancesText;
    const scan = document.querySelector("#runtimeScan");
    if (scan) scan.textContent = view.scanText;
    const blocked = document.querySelector("#runtimeBlocked");
    if (blocked) blocked.textContent = view.blockedText;
    applyBlockedStarts(view.blockedStarts);
  }

  function main() {
    const host = document.querySelector("main");
    if (!host || bound) return;
    bound = true;
    host.appendChild(buildCard());
  }

  if (typeof document !== "undefined") {
    // index.html 把脚本放在 </body> 之前：此时 DOMContentLoaded 还没触发，
    // 但用 readyState 判一次更稳（脚本被动态插入时会晚于该事件）。
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", main);
    } else {
      main();
    }
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { runtimeGuardView, applyBlockedStarts, renderRuntimeGuard };
  } else if (typeof window !== "undefined") {
    window.renderRuntimeGuard = renderRuntimeGuard;
    // 也把纯函数挂出去：离线预览/人工核对三种状态时用得上（不需要跑后端）。
    window.runtimeGuardView = runtimeGuardView;
  }
})();

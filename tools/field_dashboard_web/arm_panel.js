/*
 * 机械臂联调面板（0x20 / 0x21）。
 *
 * 设计：整个卡片由本文件在加载时自己插入 DOM，因此 index.html 只需要一行
 * <script src="/arm_panel.js"></script>。这样新增面板与其它面板互不干扰，
 * 也方便在多个 agent / 多次改动之间保持低冲突。
 *
 * 它做什么：
 *   1. 一键跑五级联调自检（结果按“名称/期望/实际/通过”排成表格）；
 *   2. 手动下发单关节 ARM_SET（不想开终端时用）；
 *   3. 把“零位移参考角度”直接列出来，照着填就不会让机械臂乱动。
 *
 * 证据边界：页面上的 PASS 只代表服务返回成功。机械臂纯开环无位置反馈，
 * 位移/方向/幅度必须现场目视确认。
 */
(function () {
  "use strict";

  const JOINT_LABELS = { 0: "腰/云盘", 1: "肩", 2: "肘", 3: "腕", 4: "爪" };
  // 与 STM32 arm.c 的 ARM_SAFE_PULSE_US 对应的角度（由后端 /api/arm/selftest 给出）。
  const STAGE_TITLES = {
    link: "链路与授权（不动机构）",
    arm_path: "ARM_SET 通路自检（零位移，机械臂不应动）",
    gripper: "夹爪闭环 GRAB/RELEASE（爪会开合）",
    timeout: "超时错误码 3020（腕约 5°）",
    boundaries: "停止与边界（升降应报 3010）",
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  async function post(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    let payload;
    try {
      payload = await response.json();
    } catch (error) {
      throw new Error(`HTTP ${response.status}: 响应不是 JSON`);
    }
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    return payload;
  }

  function buildCard() {
    const section = el("section", "card wide");
    section.id = "armPanel";
    section.appendChild(el("h2", null, "机械臂联调（0x20 / 0x21）"));
    section.appendChild(
      el(
        "p",
        "hint",
        "纯开环：机械臂没有位置反馈。页面上的 PASS 只代表“命令被接受并走完流程”，" +
          "不代表舵机真的到位——位移/方向/幅度必须目视确认。"
      )
    );

    const stageBar = el("div", "toolbar");
    stageBar.id = "armStages";
    section.appendChild(stageBar);

    const verdict = el("p", null, "尚未运行");
    verdict.id = "armVerdict";
    verdict.setAttribute("role", "status");
    section.appendChild(verdict);

    const table = el("table", "chain-table");
    table.id = "armSteps";
    section.appendChild(table);

    // --- 手动 ARM_SET ---
    const manual = el("div", "toolbar");
    manual.appendChild(el("strong", null, "手动 ARM_SET"));
    const joint = el("select");
    joint.id = "armJoint";
    for (const [value, label] of Object.entries(JOINT_LABELS)) {
      const option = el("option", null, `${value} ${label}`);
      option.value = value;
      joint.appendChild(option);
    }
    manual.appendChild(joint);
    const angle = el("input");
    angle.id = "armAngle";
    angle.type = "number";
    angle.step = "1";
    angle.min = "0";
    angle.max = "270";
    angle.value = "135";
    manual.appendChild(angle);
    manual.appendChild(el("span", null, "度"));
    const timeout = el("input");
    timeout.id = "armTimeout";
    timeout.type = "number";
    timeout.step = "1";
    timeout.min = "0.3";
    timeout.value = "8";
    manual.appendChild(timeout);
    manual.appendChild(el("span", null, "秒超时"));
    const send = el("button", null, "下发 ARM_SET");
    send.id = "armSend";
    manual.appendChild(send);
    section.appendChild(manual);

    const reference = el("div");
    reference.id = "armReference";
    section.appendChild(reference);

    const result = el("pre", null, "暂无结果");
    result.id = "armResult";
    section.appendChild(result);

    section.appendChild(
      el(
        "p",
        "hint",
        "零位移参考：对每个关节下发下面这些角度，理论上机械臂不动，" +
          "可以在几乎没有机械风险的情况下验证整条链路。爪子不在其中（走 GRAB/RELEASE）。"
      )
    );
    return section;
  }

  function renderSteps(container, steps) {
    container.textContent = "";
    const head = el("tr");
    for (const title of ["检查项", "期望", "实际", "结果", "备注"]) {
      head.appendChild(el("th", null, title));
    }
    container.appendChild(head);
    for (const item of steps) {
      const row = el("tr");
      row.className = item.ok ? "chain-ok" : "chain-bad";
      row.appendChild(el("td", null, item.name));
      row.appendChild(el("td", null, item.expect));
      row.appendChild(el("td", null, item.actual));
      row.appendChild(el("td", null, item.ok ? "PASS" : "FAIL"));
      row.appendChild(el("td", null, item.note || ""));
      container.appendChild(row);
    }
  }

  function renderReference(container, steps) {
    container.textContent = "";
    const targets = [];
    for (const item of steps) {
      const match = /^ARM_SET (\S+) (-?[\d.]+)°$/.exec(item.name);
      if (match) targets.push({ joint: match[1], angle: match[2], ok: item.ok });
    }
    if (!targets.length) {
      container.textContent =
        "（跑一次“ARM_SET 通路自检”之后，这里会列出每个关节的零位移参考角度）";
      return;
    }
    const line = targets.map((t) => `${t.joint} ${t.angle}°`).join("　|　");
    container.textContent = "零位移参考角度：" + line;
  }

  async function runStage(stage, verdict, table, reference, result) {
    table.textContent = "";
    result.textContent = "执行中…（夹爪/超时阶段会有真实动作，请把手放在遥控上）";
    verdict.textContent = `运行中：${STAGE_TITLES[stage] || stage}`;
    try {
      const payload = await post("/api/arm/selftest", { stage });
      renderSteps(table, payload.steps || []);
      if (stage === "arm_path") renderReference(reference, payload.steps || []);
      verdict.textContent = payload.passed
        ? `PASS — ${STAGE_TITLES[stage] || stage}：全部检查通过`
        : `FAIL — ${STAGE_TITLES[stage] || stage}：见下表失败项`;
      verdict.style.color = payload.passed ? "#177245" : "#b3261e";
      result.textContent = JSON.stringify(payload, null, 2);
    } catch (error) {
      verdict.textContent = `错误：${error.message}`;
      verdict.style.color = "#b3261e";
      result.textContent = String(error.message);
    }
  }

  async function main() {
    const main = document.querySelector("main");
    if (!main) return;
    const section = buildCard();
    main.appendChild(section);

    const stageBar = section.querySelector("#armStages");
    const verdict = section.querySelector("#armVerdict");
    const table = section.querySelector("#armSteps");
    const reference = section.querySelector("#armReference");
    const result = section.querySelector("#armResult");

    let stages = Object.entries(STAGE_TITLES).map(([stage, label]) => ({ stage, label }));
    try {
      const plan = await post("/api/arm/selftest", { stage: "" });
      if (Array.isArray(plan.stages) && plan.stages.length) stages = plan.stages;
    } catch (error) {
      verdict.textContent = `无法读取自检清单：${error.message}`;
    }

    for (const stage of stages) {
      const button = el("button", null, stage.label || STAGE_TITLES[stage.stage] || stage.stage);
      button.addEventListener("click", () => {
        button.disabled = true;
        runStage(stage.stage, verdict, table, reference, result).finally(() => {
          button.disabled = false;
        });
      });
      stageBar.appendChild(button);
    }

    section.querySelector("#armSend").addEventListener("click", async () => {
      const body = {
        joint: Number(section.querySelector("#armJoint").value),
        angle_deg: Number(section.querySelector("#armAngle").value),
        timeout_s: Number(section.querySelector("#armTimeout").value),
      };
      result.textContent = "下发中…";
      try {
        const payload = await post("/api/arm/set_joint", body);
        const line = payload.result || {};
        result.textContent = JSON.stringify(payload, null, 2);
        verdict.textContent = line.success
          ? `PASS — 命令被接受（code=${line.error_code}，注意不代表舵机到位）`
          : `FAIL — code=${line.error_code} ${line.detail || ""}`;
        verdict.style.color = line.success ? "#177245" : "#b3261e";
      } catch (error) {
        verdict.textContent = `错误：${error.message}`;
        verdict.style.color = "#b3261e";
        result.textContent = String(error.message);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", main);
  } else {
    main();
  }
})();

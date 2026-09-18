/*
 * 「关节微调」卡片：像遥控器那样按住动一小步，但每一步都是绝对角度。
 *
 * 为什么需要它：固件里遥控和树莓派改的是**同一个** arm_pulse_us[]（arm.c:158）。
 * 遥控是速率控制（按住一直转、松手保持），树莓派是绝对角度（ARM_SET 直接说到 65°）。
 * 缺的从来不是"能不能动"，而是一个顺手的操作面：按住小步走、松手就停、每步留证据。
 *
 * 三条必须写在页面上的实话：
 *   1. 机械臂**没有位置反馈**。页面上那个"当前角度"是**我们发出去的目标**累加值，
 *      不是读回来的角度。0x21 状态帧里没有位置/脉宽字段。
 *   2. 松手/切走窗口/断线 = 心跳停 = 几十毫秒内自动停（dead-man，和手动驾驶同一套）。
 *   3. 每步都等固件的完成回执；失败就停并显示错误码（9010=值域未冻结、9012=爪子、
 *      3020=固件没在预算内走到、6=已有其它机构命令在跑）。
 *
 * 卡片自己插入 DOM（与 arm_panel.js 同一套做法），index.html 只需一行 script。
 */
(function () {
  "use strict";

  //: 错误码 → 人话。只列会出现的那几个，其余原样显示。
  const ERROR_HINTS = {
    6: "已有其它机构命令在跑（0x20 同一时刻只允许一条）",
    9010: "关节值域未冻结：在 ros2_ws/src/robogame_bringup/config/robot.yaml 里填 arm_joint_ranges + evidence，然后重启 bridge",
    9011: "目标被拒：角度越界或不是整度",
    9012: "爪子不走 ARM_SET：抓放请用「机械臂」卡片的夹取/释放",
    3020: "固件没在预算时间内走到目标（会保持当前姿态并锁存超时）",
  };

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

  function errorHint(code) {
    return ERROR_HINTS[code] || "";
  }

  /** 纯函数：把后端 arm_jog 状态变成页面要显示的一切（便于 node 单测）。 */
  function jogView(armJog) {
    if (!armJog || armJog.available !== true) {
      return {
        available: false,
        tone: "unknown",
        status: "微调不可用：网页服务版本过旧（snapshot 里没有 arm_jog）",
        targetText: "-",
        stepsText: "",
        hintText: "",
      };
    }
    const session = armJog.session;
    const limits = armJog.limits_deg || [0, 270];
    if (!session) {
      return {
        available: true,
        tone: "unknown",
        status: `还没有微调过。角度范围 ${limits[0]}～${limits[1]}°。按住按钮才会动，松手立即停。`,
        targetText: "-",
        stepsText: "",
        hintText: armJog.note || "",
      };
    }
    const steps = session.steps || [];
    // 后端的 last_result 一定有值；这里仍回退到最近一步——少了它，页面就只剩一句
    // "失败了"而没有错误码，人还得回去翻日志。
    const last = session.last_result || steps.slice(-1)[0] || {};
    const code = last.error_code;
    const hint = errorHint(code);
    const tone = session.active ? "ok" : (session.stop_reason ? "unknown" : "unknown");
    let status;
    if (session.active) {
      status = `正在微调 ${session.joint_label}：已走 ${session.steps_done} 步，`
        + `下一次目标 ${session.last_target_deg}°，心跳 ${session.heartbeat_age_s}s 前`;
    } else if (session.stop_reason) {
      status = `已停止：${session.stop_reason}`;
    } else {
      status = "已停止";
    }
    const stepsText = steps.slice(-6).map(
      (step) => `  第 ${step.index} 步 → ${step.angle_deg}°｜`
        + (step.success ? "成功" : `失败 code=${step.error_code} ${step.detail || ""}`),
    ).join("\n");
    return {
      available: true,
      tone,
      status,
      targetText: `${session.start_deg}° → ${session.last_target_deg}°`,
      stepsText,
      hintText: [armJog.note || "", hint].filter(Boolean).join("\n"),
      lastErrorCode: code,
      active: Boolean(session.active),
    };
  }

  let held = null;   // {token, timer} —— 只在按住期间存在

  async function post(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || "请求失败");
    }
    return payload;
  }

  function readControls(section) {
    return {
      joint: Number(section.querySelector("#jogJoint").value),
      step_deg: Number(section.querySelector("#jogStep").value),
      period_s: Number(section.querySelector("#jogPeriod").value),
      start_deg: Number(section.querySelector("#jogStart").value),
    };
  }

  async function beginHold(section, verdict, direction) {
    if (held) return;
    const controls = readControls(section);
    if (!Number.isFinite(controls.start_deg)) {
      verdict.textContent = "请先填「起点角度」——我们读不到真实角度，只能由你告诉它现在在哪";
      verdict.style.color = "#b3261e";
      return;
    }
    try {
      const started = await post("/api/arm/jog/start", { ...controls, direction });
      held = {
        token: started.token,
        timer: setInterval(() => {
          post("/api/arm/jog/heartbeat", { token: held && held.token }).catch(() => endHold(verdict));
        }, 200),
      };
      verdict.textContent = "按住中…（松手立即停）";
      verdict.style.color = "#177245";
    } catch (error) {
      verdict.textContent = `不能开始微调：${error.message}`;
      verdict.style.color = "#b3261e";
    }
  }

  function endHold(verdict) {
    if (!held) return;
    clearInterval(held.timer);
    const token = held.token;
    held = null;
    post("/api/arm/jog/stop", { token }).catch(() => {});
    if (verdict) verdict.textContent = "已松开，正在停止…";
  }

  function buildCard() {
    const section = el("section", "card");
    section.appendChild(el("h2", null, "关节微调（按住动一小步）"));

    const verdict = el("p", "route-live unknown", "读取中…");
    verdict.id = "jogVerdict";
    section.appendChild(verdict);

    const form = el("div", "toolbar");
    form.appendChild(el("span", null, "关节"));
    const joint = el("select");
    joint.id = "jogJoint";
    form.appendChild(joint);
    form.appendChild(el("span", null, "起点角度"));
    const start = el("input");
    start.id = "jogStart";
    start.type = "number";
    start.step = "1";
    form.appendChild(start);
    form.appendChild(el("span", null, "步长"));
    const step = el("select");
    step.id = "jogStep";
    form.appendChild(step);
    form.appendChild(el("span", null, "间隔"));
    const period = el("select");
    period.id = "jogPeriod";
    form.appendChild(period);
    section.appendChild(form);

    const buttons = el("div", "toolbar");
    const minus = el("button", "warn", "按住 − 角度减小");
    const plus = el("button", null, "按住 + 角度增大");
    buttons.appendChild(minus);
    buttons.appendChild(plus);
    section.appendChild(buttons);

    const live = el("div", "chain-table");
    live.id = "jogLive";
    section.appendChild(live);

    const steps = el("pre", "hint");
    steps.id = "jogSteps";
    section.appendChild(steps);

    section.appendChild(el("p", "hint",
      "按住才动、松手/切走窗口/断线立即停（心跳超时自动停）。每一步都等固件回执；"
      + "失败会停下并显示错误码。⚠️ 页面显示的「当前角度」是**我们发出去的目标**累加值，"
      + "不是读回来的——这台臂没有位置反馈。"));
    return section;
  }

  function fillOptions(section, armJog) {
    const joint = section.querySelector("#jogJoint");
    if (joint && !joint.options.length && Array.isArray(armJog.joints)) {
      for (const item of armJog.joints) {
        const option = el("option", null, `${item.id} ${item.label}（安全姿态 ${item.safe_pose_deg}°）`);
        option.value = String(item.id);
        option.dataset.safePose = String(item.safe_pose_deg);
        joint.appendChild(option);
      }
      joint.addEventListener("change", () => {
        const chosen = joint.selectedOptions[0];
        const start = section.querySelector("#jogStart");
        if (chosen && start) start.value = chosen.dataset.safePose;
      });
      const start = section.querySelector("#jogStart");
      if (start && joint.selectedOptions[0]) {
        start.value = joint.selectedOptions[0].dataset.safePose;
      }
    }
    const step = section.querySelector("#jogStep");
    if (step && !step.options.length) {
      const bounds = armJog.step_range || [1, 10];
      for (const value of [1, 2, 5, 10]) {
        if (value < bounds[0] || value > bounds[1]) continue;
        const option = el("option", null, `${value}°`);
        option.value = String(value);
        step.appendChild(option);
      }
      step.value = "5";
    }
    const period = section.querySelector("#jogPeriod");
    if (period && !period.options.length) {
      const bounds = armJog.period_range || [0.1, 1];
      for (const value of [0.2, 0.5, 1.0]) {
        if (value < bounds[0] || value > bounds[1]) continue;
        const option = el("option", null, `${value}s`);
        option.value = String(value);
        period.appendChild(option);
      }
      period.value = "0.5";
    }
  }

  function renderArmJog(snapshot) {
    const section = document.querySelector("#jogVerdict")?.closest("section");
    if (!section) return;
    const armJog = snapshot && snapshot.arm_jog;
    const view = jogView(armJog);
    const verdict = section.querySelector("#jogVerdict");
    const live = section.querySelector("#jogLive");
    const steps = section.querySelector("#jogSteps");
    if (armJog && armJog.available) fillOptions(section, armJog);
    verdict.textContent = view.status + (view.targetText !== "-" ? `　目标：${view.targetText}` : "");
    verdict.className = "route-live " + (view.tone === "ok" ? "ok" : "unknown");
    if (live) {
      live.innerHTML = view.hintText
        ? `<div class="chain-row unknown"><b>⚠️</b><span>${escapeHtml(view.hintText)}</span></div>`
        : "";
    }
    if (steps) steps.textContent = view.stepsText || "（还没有走过步）";
    // 正在微调时禁用两个按住按钮（避免同一页面重复开始）
    for (const button of section.querySelectorAll("button")) {
      if (button.textContent.startsWith("按住")) button.disabled = view.active;
    }
  }

  function main() {
    const host = document.querySelector("main");
    if (!host || document.querySelector("#jogVerdict")) return;
    const section = buildCard();
    host.appendChild(section);
    const verdict = section.querySelector("#jogVerdict");
    for (const button of section.querySelectorAll("button")) {
      const direction = button.textContent.includes("+") ? 1 : -1;
      button.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        button.setPointerCapture?.(event.pointerId);
        beginHold(section, verdict, direction);
      });
      for (const event of ["pointerup", "pointercancel", "pointerleave"]) {
        button.addEventListener(event, () => endHold(verdict));
      }
    }
    window.addEventListener("blur", () => endHold(verdict));
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) endHold(verdict);
    });
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", main);
    } else {
      main();
    }
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { jogView, errorHint, renderArmJog };
  } else if (typeof window !== "undefined") {
    window.renderArmJog = renderArmJog;
    window.jogView = jogView;
  }
})();

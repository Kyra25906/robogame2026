/*
 * 「姿态示教」卡片：把手动摆出来的姿态记下来，并能一条命令复现。
 *
 * 为什么需要它：这台臂**没有位置反馈**，所以"到某个位置"只有两种来源——
 *   ① 人看着摆出来的（遥控器 / 「关节微调」）——可靠，但做完就丢了；
 *   ② 算出来的——需要逆运动学，而连杆几何没有实测，算不了。
 * 于是唯一可行的自动化路径是：**人摆一次 → 记成具名姿态 → 以后复现**。
 *
 * 三条必须写在页面上的实话：
 *   1. 记的是**我们下发过的目标角度**，不是测量值（0x21 状态帧没有位置/脉宽字段）。
 *   2. 复现是**开环**的：固件的 SUCCEEDED 只代表输出脉宽到了目标 ±4µs，
 *      不代表舵机真到了那个角度；被挡、丢步、电压不足都不会被发现。
 *   3. **建议回放前先「回零」**：每次从同一个已知起点出发，误差才不会累积。
 *      顺序按示教时的操作顺序（顺序是机械问题，我们不自作主张重排）。
 */
(function () {
  "use strict";

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

  /** 纯函数：把后端 arm_poses 状态变成页面要显示的一切（便于 node 单测）。 */
  function poseView(armPoses) {
    if (!armPoses || armPoses.available !== true) {
      return {
        available: false, tone: "unknown",
        status: "姿态示教不可用：网页服务版本过旧（snapshot 里没有 arm_poses）",
        commandedText: "", rows: [], replayText: "", fileText: "", noticeText: "",
      };
    }
    const commanded = armPoses.commanded || {};
    const deg = commanded.commanded_deg || {};
    const commandedText = Object.keys(deg)
      .map((joint) => `${joint}→${deg[joint]}°`).join("　") || "（还没有下发过任何角度）";
    const rows = Object.keys(armPoses.poses || {}).map((name) => {
      const pose = armPoses.poses[name];
      return {
        name,
        description: pose.description || "",
        note: pose.note || "",
        isSafe: name === "安全姿态",
      };
    });
    const replay = armPoses.replay;
    let replayText = "还没有回放过。";
    let tone = "unknown";
    if (replay) {
      if (replay.active) {
        tone = "ok";
        replayText = `正在回放「${replay.name}」：第 ${replay.steps_done}/${(replay.plan || []).length} 步，`
          + `已 ${replay.elapsed_s}s`;
      } else {
        replayText = `上次回放「${replay.name}」结束：${replay.steps_done} 步`
          + (replay.stop_reason ? `（${replay.stop_reason}）` : "");
      }
    }
    const lastStep = replay && (replay.steps || []).slice(-1)[0];
    if (lastStep && !lastStep.success) {
      replayText += `　⚠️ 最后一步失败 code=${lastStep.error_code} ${lastStep.detail || ""}`;
    }
    return {
      available: true, tone,
      status: `当前下发目标（**不是测量值**）：${commandedText}`,
      commandedText, rows, replayText,
      fileText: `姿态表文件：${armPoses.file || "（未配置）"}`,
      noticeText: [armPoses.note || "", armPoses.error || ""].filter(Boolean).join("\n"),
      active: Boolean(replay && replay.active),
    };
  }

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

  function buildCard() {
    const section = el("section", "card wide");
    section.appendChild(el("h2", null, "姿态示教（记录 / 回放）"));

    const verdict = el("div", "route-live unknown", "读取中…");
    verdict.id = "poseVerdict";
    section.appendChild(verdict);

    const form = el("div", "toolbar");
    const name = el("input");
    name.id = "poseName";
    name.type = "text";
    name.maxLength = 24;
    name.placeholder = "姿态名，例如：右侧抓取位";
    form.appendChild(name);
    const note = el("input");
    note.id = "poseNote";
    note.type = "text";
    note.placeholder = "备注（可选）";
    form.appendChild(note);
    const record = el("button", null, "记录当前姿态");
    record.id = "poseRecord";
    form.appendChild(record);
    const home = el("button", "warn", "先回零（建议）");
    home.id = "poseHome";
    form.appendChild(home);
    section.appendChild(form);

    const table = el("div", "chain-table");
    table.id = "poseTable";
    section.appendChild(table);

    const replayLine = el("p", "hint");
    replayLine.id = "poseReplay";
    section.appendChild(replayLine);
    const stop = el("button", "warn", "停止回放");
    stop.id = "poseStop";
    section.appendChild(stop);

    const file = el("p", "hint");
    file.id = "poseFile";
    section.appendChild(file);
    const notice = el("p", "hint");
    notice.id = "poseNotice";
    section.appendChild(notice);
    return section;
  }

  function renderPoseTable(section, view) {
    const table = section.querySelector("#poseTable");
    if (!table) return;
    table.innerHTML = view.rows.length ? "" : '<div class="chain-row unknown"><b>—</b><span>姿态表为空</span></div>';
    for (const row of view.rows) {
      const line = document.createElement("div");
      line.className = "chain-row unknown";
      line.innerHTML = `<b>${row.isSafe ? "出厂" : "示教"}</b>`
        + `<span>${escapeHtml(row.description)}</span>`
        + `<em>${escapeHtml(row.note || "")}</em>`;
      const actions = document.createElement("span");
      const replayButton = el("button", null, "回放");
      replayButton.dataset.replay = row.name;
      const deleteButton = el("button", "warn", "删除");
      deleteButton.dataset.delete = row.name;
      if (row.isSafe) deleteButton.disabled = true;   // 安全姿态是固件事实，不许删
      actions.appendChild(replayButton);
      actions.appendChild(deleteButton);
      line.appendChild(actions);
      table.appendChild(line);
    }
  }

  function renderArmPoses(snapshot) {
    const verdict = document.querySelector("#poseVerdict");
    if (!verdict) return;
    const section = verdict.closest("section");
    const view = poseView(snapshot && snapshot.arm_poses);
    verdict.textContent = view.status;
    verdict.className = "route-live " + (view.tone === "ok" ? "ok" : "unknown");
    renderPoseTable(section, view);
    const replayLine = section.querySelector("#poseReplay");
    if (replayLine) replayLine.textContent = view.replayText;
    const file = section.querySelector("#poseFile");
    if (file) file.textContent = view.fileText;
    const notice = section.querySelector("#poseNotice");
    if (notice) notice.textContent = view.noticeText;
    const stop = section.querySelector("#poseStop");
    if (stop) stop.disabled = !view.active;
  }

  function main() {
    const host = document.querySelector("main");
    if (!host || document.querySelector("#poseVerdict")) return;
    const section = buildCard();
    host.appendChild(section);
    const verdict = section.querySelector("#poseVerdict");

    section.querySelector("#poseRecord").addEventListener("click", async () => {
      const name = section.querySelector("#poseName").value.trim();
      if (!name) {
        verdict.textContent = "请先填姿态名（例如「右侧抓取位」）";
        return;
      }
      try {
        const result = await post("/api/arm/pose/record", {
          name, note: section.querySelector("#poseNote").value.trim(),
        });
        verdict.textContent = `已记录：${result.pose.description}　→ ${result.file}`;
      } catch (error) {
        verdict.textContent = `记录失败：${error.message}`;
      }
    });

    section.querySelector("#poseHome").addEventListener("click", async () => {
      try {
        await post("/api/mechanism/home", {});
        verdict.textContent = "回零已请求：成功后「当前下发目标」会重置成安全姿态";
      } catch (error) {
        verdict.textContent = `回零失败：${error.message}`;
      }
    });

    section.querySelector("#poseStop").addEventListener("click", async () => {
      try {
        await post("/api/arm/pose/stop", {});
        verdict.textContent = "已请求停止回放";
      } catch (error) {
        verdict.textContent = `停止失败：${error.message}`;
      }
    });

    // 表里的「回放 / 删除」是每帧重建的，用事件委托，避免重建后丢监听。
    section.querySelector("#poseTable").addEventListener("click", async (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      try {
        if (target.dataset.replay) {
          const result = await post("/api/arm/pose/replay", { name: target.dataset.replay });
          verdict.textContent = `开始回放「${result.name}」：${result.steps} 步（${result.description}）`;
        } else if (target.dataset.delete) {
          await post("/api/arm/pose/delete", { name: target.dataset.delete });
          verdict.textContent = `已删除「${target.dataset.delete}」`;
        }
      } catch (error) {
        verdict.textContent = `操作失败：${error.message}`;
      }
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
    module.exports = { poseView, renderArmPoses };
  } else if (typeof window !== "undefined") {
    window.renderArmPoses = renderArmPoses;
    window.poseView = poseView;
  }
})();

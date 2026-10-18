const $ = (id) => document.getElementById(id);
const text = (tag, value, parent) => {
  const el = document.createElement(tag);
  el.textContent = value;
  parent.append(el);
  return el;
};
const fmt = (value) => (value === null ? "无法判断" : Number(value).toFixed(3));
const pct = (value) => (value === null ? "无法判断" : `${(value * 100).toFixed(0)}%`);
let reports = [];
let current = null;

function bar(parent, label, portions) {
  text("p", label, parent);
  const row = text("div", "", parent);
  row.className = "bar";
  row.setAttribute("role", "img");
  row.setAttribute("aria-label", label);
  for (const [state, value] of portions) {
    const segment = text("span", "", row);
    segment.className = state;
    segment.style.width = `${Math.max(0, Math.min(1, value)) * 100}%`;
  }
}

function validateReport(report) {
  if (report.version !== "synthetic-evaluation-demo.v2" || report.source !== "handwritten_synthetic_only" || report.real_llm !== false) {
    throw new Error("不是允许的合成报告");
  }
  if (!report.overview || !Array.isArray(report.analysis) || !report.summary || !report.summary.accuracy) {
    throw new Error("缺少概要、分析或总结");
  }
  if (!Array.isArray(report.summary.improvements) || report.summary.improvements.length === 0) {
    throw new Error("总结缺少后续改进");
  }
  if (report.analysis.some((item) => !item.title || !item.text)) {
    throw new Error("分析条目不完整");
  }
  const ids = new Set(report.evidence.map((item) => item.id));
  if (report.claims.some((claim) => claim.evidence.some((id) => !ids.has(id)))) {
    throw new Error("结论引用不存在的证据");
  }
}

function render(report) {
  validateReport(report);
  current = report;
  ["cards", "error-chart", "status-chart", "metrics", "analysis", "claims", "evidence", "limits", "improvements"].forEach((id) => {
    $(id).replaceChildren();
  });

  $("title").textContent = report.title;
  $("overview-objective").textContent = report.overview.objective;
  $("overview-data").textContent = `数据：${report.overview.data}`;
  $("overview-reading").textContent = `怎么看：${report.overview.reading}`;

  const cards = [
    ["共同成功任务", `${report.comparison.common_n} / ${report.schemes[0].success_only.requested}`],
    ["共同任务的用户数", report.comparison.users],
    ["被排除的任务", report.comparison.excluded_n],
    ["真实模型调用", "0 · 准确率未测量"],
  ];
  for (const [label, value] of cards) {
    const card = text("div", "", $("cards"));
    card.className = "card";
    text("span", label, card);
    text("strong", value, card);
  }

  if (report.comparison.common_n === 0) {
    text("p", "没有共同成功任务，不能比较 MAE。", $("error-chart")).className = "empty-chart";
  } else {
    for (const key of ["a", "b"]) {
      const metric = report.comparison[key];
      bar($("error-chart"), `${key.toUpperCase()}：MAE ${fmt(metric.mae)}，n=${metric.eligible}`, [
        ["success", metric.mae === null ? 0 : metric.mae / 4],
      ]);
    }
    text("p", "条形长度按 4 星误差上限归一化；只用于展示，不改变数值。", $("error-chart")).className = "legend";
  }

  for (const scheme of report.schemes) {
    const metric = scheme.success_only;
    bar(
      $("status-chart"),
      `${scheme.name}：成功 ${metric.counts.success} / ${metric.requested}`,
      Object.entries(metric.counts).map(([state, value]) => [state, value / metric.requested]),
    );
    text(
      "p",
      `成功 ${metric.counts.success} · 回退 ${metric.counts.degraded} · 失败 ${metric.counts.failed} · 拒绝 ${metric.counts.refused}`,
      $("status-chart"),
    ).className = "legend";
  }

  for (const scheme of report.schemes) {
    for (const [key, label] of [["success_only", "仅模型成功"], ["including_fallback", "系统含回退"]]) {
      const metric = scheme[key];
      const row = text("tr", "", $("metrics"));
      for (const cell of [
        `${scheme.name} / ${label}`,
        `${metric.eligible}/${metric.requested}`,
        fmt(metric.mae),
        fmt(metric.rmse),
        metric.within_one === null ? "无法判断" : pct(metric.within_one),
        fmt(metric.bias),
      ]) {
        text("td", cell, row);
      }
    }
  }

  for (const item of report.analysis) {
    const box = text("div", "", $("analysis"));
    box.className = "analysis-item";
    text("h3", item.title, box);
    text("p", item.text, box);
  }

  for (const claim of report.claims) {
    const box = text("div", "", $("claims"));
    box.className = "claim";
    text("p", claim.text, box);
    for (const id of claim.evidence) {
      const link = text("a", `查看证据：${id}`, box);
      link.href = `#evidence-${id}`;
    }
  }
  for (const item of report.evidence) {
    const p = text("p", `${item.label}：${fmt(item.value)}；分母 ${item.denominator}`, $("evidence"));
    p.id = `evidence-${item.id}`;
  }

  $("accuracy-status").textContent = report.summary.accuracy.status;
  $("accuracy-detail").textContent = report.summary.accuracy.detail;
  $("accuracy-checked").textContent = report.summary.accuracy.checked;
  $("summary-conclusion").textContent = report.summary.conclusion;
  report.summary.improvements.forEach((item) => text("li", item, $("improvements")));
  report.limits.forEach((item) => text("li", item, $("limits")));

  $("provenance").textContent = [
    `工具版本：${report.version}`,
    `输入指纹：${report.input_fingerprint}`,
    `报告指纹：${report.report_fingerprint}`,
    "区间与显著性：未计算",
    "解释：确定性模板，非 LLM",
  ].join("\n");

  $("result").hidden = false;
  document.querySelectorAll("#flow li").forEach((el) => el.classList.add("done"));
  $("status").textContent = "合成报告 v2 已校验并渲染。Python 数学计算在构建阶段完成，页面没有调用模型。";
  $("result").scrollIntoView({ behavior: "smooth", block: "start" });
}

$("run").addEventListener("click", () => {
  try {
    render(reports.find((report) => report.scenario === $("scenario").value));
  } catch {
    current = null;
    $("result").hidden = true;
    $("status").textContent = "报告校验失败，未展示指标。";
  }
});

$("scenario").addEventListener("change", () => {
  current = null;
  $("result").hidden = true;
  document.querySelectorAll("#flow li").forEach((el) => el.classList.remove("done"));
  $("status").textContent = "场景已更改，请重新查看，旧结论已隐藏。";
});

$("download").addEventListener("click", () => {
  if (!current) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(current, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `synthetic-demo-${current.scenario}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

try {
  const response = await fetch("reports.json");
  if (!response.ok) throw new Error("report load failed");
  reports = await response.json();
  if (reports.length !== 3) throw new Error("unexpected report count");
  $("run").disabled = false;
  $("status").textContent = "三个合成场景已就绪。请选择并查看概要、可视化、分析和总结。";
} catch {
  $("status").textContent = "示例报告加载失败，请检查构建或网络；未生成任何假数据。";
}

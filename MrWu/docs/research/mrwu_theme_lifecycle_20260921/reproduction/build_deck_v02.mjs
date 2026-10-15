import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { Presentation, PresentationFile } from "file:///C:/Users/HarryWu_IOKI/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";

const workspaceDir = String.raw`C:\Project Rocket\02 Strategy Analysis\theme_lifecycle_20260921`;
const runDir = path.join(workspaceDir, "run_v1");
const runV2Dir = path.join(workspaceDir, "run_v2");
const synDir = path.join(runDir, "synthesis_output");
const analysisV2Dir = path.join(runV2Dir, "analysis_output");
const buildDir = path.join(runV2Dir, "ppt_build");
const outputDir = path.join(workspaceDir, "deliverables");
const SKILL_DIR = String.raw`C:\Users\HarryWu_IOKI\.codex\plugins\cache\openai-primary-runtime\presentations\26.909.12148\skills\presentations`;
const RUNTIME_PYTHON = String.raw`C:\Users\HarryWu_IOKI\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`;
const FINAL_PPTX = path.join(outputDir, "A股题材生命周期与龙位更替研究_20251030_20260831_v0.2.pptx");

const utils = await import(pathToFileURL(path.join(SKILL_DIR, "container_tools/artifact_tool_utils.mjs")).href);
const { resolvePresentationFont, applyPresentationChartFont, finalizePresentation } = utils;
const family = resolvePresentationFont({ fontFamily: "Microsoft YaHei" });

const C = {
  navy: "#14213D", ink: "#172033", paper: "#F7F3EA", white: "#FFFFFF",
  red: "#D85C41", teal: "#2A9D8F", gold: "#E9C46A", blue: "#4777B8",
  pale: "#EAEFF5", paleRed: "#F7E4DE", paleTeal: "#DDEFEA", paleGold: "#F7EAC5",
  gray: "#667085", light: "#D0D5DD", charcoal: "#344054", green: "#4C956C",
};

function parseCsv(text) {
  const rows = [];
  let row = [], cell = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') { row.push(cell); cell = ""; }
    else if (ch === '\n') { row.push(cell.replace(/\r$/, "")); rows.push(row); row = []; cell = ""; }
    else cell += ch;
  }
  if (cell.length || row.length) { row.push(cell); rows.push(row); }
  const headers = rows.shift();
  return rows.filter(r => r.length && r.some(v => v !== "")).map(r => Object.fromEntries(headers.map((h, i) => [h, r[i] ?? ""])));
}

const readJson = async p => JSON.parse(await fs.readFile(p, "utf8"));
const readCsv = async p => parseCsv((await fs.readFile(p, "utf8")).replace(/^\uFEFF/, ""));
const summary = await readJson(path.join(synDir, "SYNTHESIS_SUMMARY.json"));
const cohort = await readCsv(path.join(synDir, "LIFECYCLE_COHORT_SUMMARY.csv"));
const hierarchy = await readCsv(path.join(synDir, "DAILY_THEME_HIERARCHY.csv"));
const leaderEvidence = await readCsv(path.join(synDir, "LEADER_ROLE_EVIDENCE.csv"));
const themeProfile = await readCsv(path.join(synDir, "THEME_PROFILE.csv"));
const ytdSummary = await readCsv(path.join(synDir, "LEADER_YTD_SUMMARY.csv"));
const ytdFull = await readCsv(path.join(synDir, "LEADER_YTD_2026_FULL.csv"));
const v02 = await readJson(path.join(analysisV2Dir, "SUMMARY_V02.json"));
const recurrentSummary = await readCsv(path.join(analysisV2Dir, "RECURRENT_THEME_DRAGON_SUMMARY.csv"));
const recurrentDetail = await readCsv(path.join(analysisV2Dir, "RECURRENT_THEME_DRAGON_DETAIL.csv"));
const priorDragonSummary = await readCsv(path.join(analysisV2Dir, "RECURRENT_PRIOR_DRAGON_LATER_SUMMARY.csv"));
const singleWaveAggregate = await readCsv(path.join(analysisV2Dir, "SINGLE_WAVE_MULTIDAY_DRAGON_AGGREGATE.csv"));
const singleWaveCases = await readCsv(path.join(analysisV2Dir, "SINGLE_WAVE_MULTIDAY_DRAGON_CASES.csv"));
const singleWaveDaily = await readCsv(path.join(analysisV2Dir, "SINGLE_WAVE_MULTIDAY_DRAGON_DAILY.csv"));
const leaderReturnsExtended = await readCsv(path.join(analysisV2Dir, "LEADER_RETURNS_EXTENDED.csv"));

const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });

function addText(slide, text, x, y, w, h, opts = {}) {
  const s = slide.shapes.add({
    geometry: "textbox", position: { left: x, top: y, width: w, height: h },
    fill: opts.fill ?? "none", line: { fill: opts.line ?? "none", width: opts.lineWidth ?? 0 },
  });
  s.text = String(text);
  s.text.style = {
    typeface: family, fontSize: opts.size ?? 24, bold: opts.bold ?? false,
    color: opts.color ?? C.ink, alignment: opts.align ?? "left",
    verticalAlignment: opts.valign ?? "middle", autoFit: opts.autoFit ?? "shrinkText",
  };
  return s;
}

function rect(slide, x, y, w, h, fill, radius = false, line = "none", lineWidth = 0) {
  return slide.shapes.add({
    geometry: radius ? "roundRect" : "rect", position: { left: x, top: y, width: w, height: h },
    fill, line: { fill: line, width: lineWidth },
  });
}

function baseSlide(title, kicker, page, dark = false) {
  const slide = presentation.slides.add();
  slide.background.fill = dark ? C.navy : C.paper;
  if (!dark) rect(slide, 0, 0, 18, 720, C.red);
  addText(slide, kicker.toUpperCase(), 72, 30, 420, 26, { size: 13, bold: true, color: dark ? C.gold : C.red });
  addText(slide, title, 72, 58, 1120, 62, { size: 34, bold: true, color: dark ? C.white : C.navy });
  rect(slide, 72, 128, 1136, 2, dark ? "#55627A" : C.light);
  addText(slide, `2025-10-30 — 2026-08-31  ·  描述性研究`, 72, 676, 720, 20, { size: 11, color: dark ? "#B8C2D8" : C.gray });
  addText(slide, String(page).padStart(2, "0"), 1160, 673, 48, 22, { size: 12, bold: true, color: dark ? C.gold : C.red, align: "right" });
  return slide;
}

function pill(slide, text, x, y, w, fill, color = C.white) {
  rect(slide, x, y, w, 32, fill, true);
  addText(slide, text, x + 10, y + 2, w - 20, 28, { size: 13, bold: true, color, align: "center" });
}

function kpi(slide, value, label, x, y, w, color = C.red, sub = "") {
  rect(slide, x, y, w, 132, C.white, true, "#E3DED4", 1);
  addText(slide, value, x + 18, y + 16, w - 36, 50, { size: 36, bold: true, color });
  addText(slide, label, x + 18, y + 68, w - 36, 30, { size: 15, bold: true, color: C.ink });
  if (sub) addText(slide, sub, x + 18, y + 98, w - 36, 24, { size: 11, color: C.gray });
}

function callout(slide, text, x, y, w, h, fill = C.paleGold, color = C.ink) {
  rect(slide, x, y, w, h, fill, true);
  addText(slide, text, x + 16, y + 10, w - 32, h - 20, { size: 17, bold: true, color });
}

function note(slide, sources, text = "") {
  slide.speakerNotes.textFrame.setText(`来源：${sources}\n${text}`);
}

function addTable(slide, values, x, y, w, h, widths, opts = {}) {
  const table = slide.tables.add({ rows: values.length, columns: values[0].length, left: x, top: y, width: w, height: h, values, columnWidths: widths });
  table.borders.assign({ style: "solid", fill: "#D9D5CC", width: 1 });
  table.cells.block({ row: 0, column: 0, rowCount: 1, columnCount: values[0].length }).assign({
    fill: C.navy, textStyle: { typeface: family, fontSize: opts.headerSize ?? 13, bold: true, color: C.white },
    margins: { left: 8, right: 8, top: 5, bottom: 5 },
  });
  if (values.length > 1) table.cells.block({ row: 1, column: 0, rowCount: values.length - 1, columnCount: values[0].length }).assign({
    fill: C.white, textStyle: { typeface: family, fontSize: opts.bodySize ?? 12, color: C.ink },
    margins: { left: 8, right: 8, top: 4, bottom: 4 },
  });
  for (let r = 1; r < values.length; r++) if (r % 2 === 0) {
    table.cells.block({ row: r, column: 0, rowCount: 1, columnCount: values[0].length }).fill = "#F1EEE6";
  }
  return table;
}

function chartBar(slide, cfg) {
  const normalizedSeries = cfg.series.map(series => ({
    ...series,
    values: series.values.map(value => Number(Number(value).toFixed(4))),
  }));
  const chart = slide.charts.add("bar", {
    position: { left: cfg.x, top: cfg.y, width: cfg.w, height: cfg.h },
    categories: cfg.categories,
    series: normalizedSeries,
    barOptions: { direction: cfg.direction ?? "bar", grouping: cfg.grouping ?? "clustered", gapWidth: 55 },
    hasLegend: cfg.hasLegend ?? (cfg.series.length > 1),
    legend: { position: "bottom", overlay: false, textStyle: { fontSize: 12, fill: C.gray } },
    xAxis: cfg.direction === "column"
      ? { textStyle: { fontSize: 12, fill: C.gray }, line: { fill: C.light, width: 1 } }
      : { visible: false, majorGridlines: null },
    yAxis: cfg.direction === "column"
      ? { visible: true, min: cfg.min ?? 0, max: cfg.max, numberFormatCode: cfg.format ?? "0.0", majorGridlines: { fill: "#E5E2DA", width: 1 }, textStyle: { fontSize: 11, fill: C.gray } }
      : { textStyle: { fontSize: 13, fill: C.charcoal }, line: { fill: C.light, width: 1 } },
    dataLabels: { showValue: true, position: cfg.labelPosition ?? "outEnd", textStyle: { fontSize: 12, bold: true, fill: C.ink } },
    chartFill: C.white, plotAreaFill: C.white, chartLine: { fill: "#E3DED4", width: 1 },
  });
  applyPresentationChartFont(chart, { fontFamily: family });
  return chart;
}

function pct(v, d = 1) {
  const n = Math.abs(Number(v)) < 0.0005 ? 0 : Number(v);
  return `${(n * 100).toFixed(d)}%`;
}
function num(v, d = 1) { return Number(v).toFixed(d); }
function pctPoint(v, d = 1) { return `${Number(v).toFixed(d)}%`; }
function namesFromTop3(raw) {
  if (!raw) return "—";
  return raw.split(";").map(x => {
    const role = x.match(/^Dragon([123])=/)?.[1];
    if (!role) return x;
    const m = x.match(/^[^=]+=[^:]*:(.*)$/);
    return `龙${role} ${m?.[1] || "—"}`;
  }).join(" / ");
}
function cleanNameList(raw, limit = 4) {
  if (!raw) return "—";
  const out = raw.split(";").filter(Boolean).map(x => x.includes(":") ? x.split(":").slice(1).join(":") : x);
  return out.slice(0, limit).join("、") + (out.length > limit ? ` 等${out.length}只` : "");
}
function cohortVal(group, metric, field = "median") { return Number(cohort.find(r => r.cohort === group && r.metric === metric)[field]); }
function leaderVal(role, metric, field) { return Number(leaderEvidence.find(r => r.initial_role === role && r.source_metric === metric)[field]); }

if (false) {
// Legacy v0.3 slides retained as disabled source reference.
// 1 — cover
{
  const s = presentation.slides.add();
  s.background.fill = C.navy;
  rect(s, 0, 0, 22, 720, C.red);
  pill(s, "研究报告 v0.1", 82, 70, 148, C.red);
  addText(s, "题材的波动、交接\n与龙头层级", 82, 146, 830, 190, { size: 56, bold: true, color: C.white });
  addText(s, "2025-10-30 至 2026-08-31｜开盘啦涨停题材｜描述性证据", 86, 356, 920, 40, { size: 20, color: "#DCE3F0" });
  rect(s, 86, 430, 1080, 1, "#52617D");
  addText(s, "我们研究的不是“哪个题材明天涨”，而是：题材如何活跃、如何换位，龙头层级是否真的有意义。", 86, 464, 950, 86, { size: 25, bold: true, color: C.gold });
  addText(s, "基于 205 个交易日 · 137 个活跃原始题材 · 793 段行情", 86, 624, 720, 30, { size: 15, color: "#B8C2D8" });
  addText(s, "01", 1144, 642, 64, 30, { size: 13, bold: true, color: C.gold, align: "right" });
  note(s, "SYNTHESIS_SUMMARY.json；RESEARCH_SPEC_v0.1.json", "封面数字均来自已验收描述性汇总。无新闻、无策略结论。");
}

// 2 — research frame
{
  const s = baseSlide("先定义研究对象，再讨论任何策略", "研究框架", 2);
  const xs = [76, 382, 688, 994];
  const labels = [
    ["题材", "原始标签与每日梯队"], ["行情段", "启动—活跃—退潮—再启动"],
    ["个股层级", "冻结日 Leader1/2/3/后排"], ["结果", "纯前瞻表现、换人、中军状态"],
  ];
  labels.forEach((d, i) => {
    rect(s, xs[i], 190, 230, 130, i === 0 ? C.paleRed : i === 1 ? C.paleGold : i === 2 ? C.paleTeal : C.pale, true);
    addText(s, d[0], xs[i] + 18, 208, 194, 34, { size: 23, bold: true, color: C.navy });
    addText(s, d[1], xs[i] + 18, 252, 194, 50, { size: 14, color: C.charcoal });
    if (i < 3) addText(s, "→", xs[i] + 240, 232, 54, 40, { size: 30, bold: true, color: C.red, align: "center" });
  });
  callout(s, "本轮不回答：买点、卖点、仓位、开盘30分钟执行规则。", 76, 380, 1120, 62, C.navy, C.white);
  addText(s, "回答顺序", 76, 482, 140, 28, { size: 15, bold: true, color: C.red });
  addText(s, "① 哪些题材活跃过、是否反复  →  ② 主线如何交接  →  ③ 龙头层级是否优于后排  →  ④ 中军能否解释持续性", 76, 520, 1110, 70, { size: 22, bold: true, color: C.ink });
  note(s, "RESEARCH_SPEC_v0.1.json；ANALYSIS_REPORT.md", "核心框架：板块—资金流是龙骨；本报告只建立板块与个股层级的历史证据。");
}

// 3 — data and rules
{
  const s = baseSlide("数据基准与冻结口径", "研究边界", 3);
  kpi(s, "205", "交易日", 76, 160, 250, C.red, "2025-10-30—2026-08-31");
  kpi(s, "19,716", "合格涨停/炸板记录", 346, 160, 250, C.teal, "SH/SZ；排除北交所与ST前缀");
  kpi(s, "14,453", "封住涨停", 616, 160, 250, C.blue, "另有 5,263 条炸板");
  kpi(s, "281", "出现过涨停的原始标签", 886, 160, 250, C.gold, "其中 137 个达到活跃标准");
  const vals = [
    ["对象", "冻结定义", "本轮限制"],
    ["题材标签", "优先使用开盘啦 lu_desc 原始标签", "不合并别名，不做大类/催化归并"],
    ["活跃日", "≥3 个封板；或 ≥2 个且至少1个连板", "高度不能单独压过宽度"],
    ["行情段", "退潮日后重新活跃即新一段", "零涨停或极端断档视为退潮"],
    ["个股角色", "首次进入展示集合当日冻结", "实现龙头另列，明确后视镜"],
  ];
  addTable(s, vals, 76, 330, 1060, 268, [180, 420, 460], { bodySize: 13 });
  note(s, "RESEARCH_SPEC_v0.1.json；DATA_QUALITY_STAGE1.json；SUMMARY_STAGE2.json", "所有定义在结果检视前冻结。原始SQLite只读，未读取新闻。");
}

// 4 — mother set
{
  const s = baseSlide("机会母体：多数题材会回来，但不是连续活跃", "题材母体", 4);
  chartBar(s, {
    x: 76, y: 170, w: 720, h: 400, direction: "bar",
    categories: ["反复活跃", "单轮多日", "仅活跃1日"],
    series: [{ name: "原始题材数", values: [80, 10, 47], fill: C.red }],
    hasLegend: false,
  });
  kpi(s, "137", "达到活跃标准", 846, 174, 320, C.navy, "完整研究母体");
  kpi(s, "90", "进入过每日展示", 846, 326, 320, C.teal, "Top 5 或 ≥10 个涨停");
  callout(s, "80 / 137 = 58.4%\n至少经历两段行情", 846, 478, 320, 104, C.paleGold, C.navy);
  note(s, "THEME_PROFILE.csv；LIFECYCLE_COHORT_SUMMARY.csv", "反复活跃按同一原始标签在退潮日后重新形成行情段定义。");
}

// 5 — cohort table
{
  const s = baseSlide("三类生命周期：反复活跃 ≠ 单段持续很久", "生命周期", 5);
  const groups = [
    ["反复活跃", "recurrent_union"], ["单轮多日", "single_wave_multiday"], ["一日型", "single_wave_one_day"],
  ];
  const vals = [["类别", "题材数", "每题材行情段\n中位数", "活跃日\n中位数", "峰值涨停数\n中位数", "峰值高度\n中位数", "进入展示比例"]];
  for (const [label, key] of groups) vals.push([
    label,
    String(Number(cohort.find(r => r.cohort === key).theme_count)),
    num(cohortVal(key, "episode_count"), 1),
    num(cohortVal(key, "active_days"), 1),
    num(cohortVal(key, "peak_limit_up_count"), 1),
    num(cohortVal(key, "peak_max_board_count"), 1),
    pct(cohort.find(r => r.cohort === key).headline_share),
  ]);
  addTable(s, vals, 76, 182, 1120, 260, [210, 120, 170, 135, 175, 150, 160], { headerSize: 12, bodySize: 15 });
  callout(s, "反复题材的中位数是 6 段行情、9 个活跃日；但单段行情的中位持续仍只有 1 天。", 76, 476, 1120, 74, C.paleRed, C.navy);
  addText(s, "含义：同一个题材名称会反复被资金使用；不能把几个月里的活跃简单当成一轮完整生命周期。", 90, 566, 1090, 54, { size: 19, bold: true, color: C.charcoal });
  note(s, "LIFECYCLE_COHORT_SUMMARY.csv；THEME_EPISODES.csv", "表中均为描述性中位数；反复题材的单段时长分布高度偏斜。");
}

// 6 — cohort intensity
{
  const s = baseSlide("反复题材更深，但“一日高潮”通常更窄更低", "生命周期", 6);
  chartBar(s, {
    x: 76, y: 178, w: 540, h: 330, direction: "column", max: 10,
    categories: ["反复活跃", "单轮多日", "一日型"],
    series: [{ name: "峰值涨停数（中位）", values: [8, 7, 3], fill: C.red }], hasLegend: false,
  });
  chartBar(s, {
    x: 664, y: 178, w: 540, h: 330, direction: "column", max: 6,
    categories: ["反复活跃", "单轮多日", "一日型"],
    series: [{ name: "峰值连板高度（中位）", values: [5, 3, 2], fill: C.teal }], hasLegend: false,
  });
  callout(s, "证据支持“反复活跃题材的宽度与高度更深”，但不能据此断言它会持续上涨。", 76, 540, 1128, 64, C.navy, C.white);
  note(s, "LIFECYCLE_COHORT_SUMMARY.csv", "图表为中位数。宽度=行情段峰值封板数；高度=峰值连板高度。");
}

// 7 — monthly timeline
{
  const s = baseSlide("按时间序列：每月第一题材仍在不断换位", "205日时间轴", 7);
  const monthCounts = new Map();
  for (const r of hierarchy) {
    const m = r.trade_date.slice(0, 7);
    if (!monthCounts.has(m)) monthCounts.set(m, new Map());
    const mp = monthCounts.get(m); mp.set(r.rank1_theme, (mp.get(r.rank1_theme) ?? 0) + 1);
  }
  const values = [["月份", "第一题材出现最多", "天数", "第二", "第三"]];
  for (const [m, mp] of [...monthCounts.entries()].sort()) {
    const arr = [...mp.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "zh-CN"));
    values.push([m, arr[0]?.[0] ?? "", String(arr[0]?.[1] ?? 0), arr[1]?.[0] ?? "", arr[2]?.[0] ?? ""]);
  }
  addTable(s, values, 76, 160, 760, 460, [125, 230, 85, 170, 150], { bodySize: 12 });
  kpi(s, "205", "逐日层级已全部枚举", 880, 174, 290, C.red, "DAILY_THEME_HIERARCHY.csv");
  callout(s, "月度只做压缩展示\n同月内部仍会多次交接", 880, 334, 290, 104, C.paleGold, C.navy);
  addText(s, "完整表包含每日前五题材、涨停宽度、最高板、梯队完整度与交接标签。", 880, 470, 286, 110, { size: 17, bold: true, color: C.charcoal });
  note(s, "DAILY_THEME_HIERARCHY.csv", "月度表按每日 rank1_theme 出现天数统计，仅用于压缩展示，不替代逐日序列。");
}

// 8 — prior top1 fate
{
  const s = baseSlide("昨日第一，次日只有 27% 仍是第一", "题材交接", 8);
  chartBar(s, {
    x: 76, y: 170, w: 760, h: 390, direction: "bar", max: 50, format: '0.0"%"',
    categories: ["仍是第一", "降至第2—5", "退出前五"],
    series: [{ name: "占204组相邻日", values: [26.96, 41.18, 31.86], fill: C.red, valuesFormatCode: '0.0"%"' }],
    hasLegend: false,
  });
  kpi(s, "41.2%", "降级但仍在前五", 874, 180, 290, C.teal, "不是简单“延续/熄火”二分");
  kpi(s, "31.9%", "直接退出前五", 874, 336, 290, C.red, "主线失速并不少见");
  callout(s, "前一晚判断必须保留候选集；次日需要重新验证资金选择。", 874, 492, 290, 92, C.navy, C.white);
  note(s, "ROTATION_SUMMARY.csv；DAILY_THEME_HIERARCHY.csv", "分母=204组相邻交易日。百分比按未四舍五入计数闭合。");
}

// 9 — current top1 origin
{
  const s = baseSlide("当日新第一，73% 不是昨日第一", "题材交接", 9);
  chartBar(s, {
    x: 76, y: 170, w: 760, h: 390, direction: "bar", max: 45, format: '0.0"%"',
    categories: ["昨日第一延续", "昨日第2—5接力", "昨日前五外跃迁"],
    series: [{ name: "当日第一来源", values: [26.96, 37.75, 35.29], fill: C.teal, valuesFormatCode: '0.0"%"' }],
    hasLegend: false,
  });
  callout(s, "37.7%", 890, 188, 250, 74, C.paleTeal, C.navy);
  addText(s, "来自昨日次主线", 890, 270, 250, 36, { size: 18, bold: true, color: C.charcoal, align: "center" });
  callout(s, "35.3%", 890, 336, 250, 74, C.paleRed, C.navy);
  addText(s, "来自昨日前五之外", 890, 418, 250, 36, { size: 18, bold: true, color: C.charcoal, align: "center" });
  addText(s, "研究上必须同时覆盖：旧主线、次主线候选、新出现方向。", 878, 498, 278, 72, { size: 18, bold: true, color: C.navy, align: "center" });
  note(s, "ROTATION_SUMMARY.csv", "current_top1_origin 按上一交易日前五中的排名归类。");
}

// 10 — exemplar hierarchy day
{
  const peak = [...hierarchy].sort((a, b) => Number(b.rank1_limit_up_count) - Number(a.rank1_limit_up_count))[0];
  const s = baseSlide(`梯队观察样例：${peak.trade_date}`, "每日前五", 10);
  const values = [["排名", "题材", "封板数", "最高板", "梯队完整度", "综合分"]];
  for (let i = 1; i <= 5; i++) values.push([
    `#${i}`, peak[`rank${i}_theme`], peak[`rank${i}_limit_up_count`], peak[`rank${i}_max_board_count`],
    num(peak[`rank${i}_ladder_completeness`], 2), num(peak[`rank${i}_combined_score`], 2),
  ]);
  addTable(s, values, 76, 180, 900, 330, [90, 250, 130, 120, 160, 150], { bodySize: 15 });
  callout(s, "不是只看“最高板”", 1010, 186, 170, 64, C.paleRed, C.navy);
  addText(s, "排名同时使用：\n\n• 涨停宽度\n• 高度与深度\n• 梯队完整度\n• 炸板质量", 1010, 266, 180, 214, { size: 18, bold: true, color: C.charcoal });
  addText(s, "完整 205 日 × 前五题材已另附 CSV。", 84, 548, 900, 42, { size: 18, bold: true, color: C.red });
  note(s, "DAILY_THEME_HIERARCHY.csv；DAILY_THEME_TOP5.csv", "样例取每日第一题材封板数最高的交易日；用于展示表结构，不作案例结论。");
}

// 11 — role freeze vs hindsight
{
  const s = baseSlide("必须分开：冻结角色 vs. 实现龙头", "个股层级", 11);
  rect(s, 76, 178, 500, 306, C.paleTeal, true);
  addText(s, "冻结角色｜可前瞻", 104, 198, 440, 42, { size: 25, bold: true, color: C.navy });
  addText(s, "首次进入每日展示集合当日，用当时信息排序：\n\n1. 连板高度\n2. 封板优先于炸板\n3. 封板时间\n4. 封单/成交额\n5. 代码确定性打破并列", 104, 252, 430, 210, { size: 18, color: C.charcoal });
  rect(s, 704, 178, 500, 306, C.paleRed, true);
  addText(s, "实现龙头｜后视镜", 732, 198, 440, 42, { size: 25, bold: true, color: C.navy });
  addText(s, "看完整段行情后才知道：\n\n• 最高达到几板\n• 区间路径峰值\n• 最早达到日期\n\n只能解释“最后谁走出来”，不能替代当时选股。", 732, 252, 430, 210, { size: 18, color: C.charcoal });
  addText(s, "→", 592, 294, 96, 60, { size: 42, bold: true, color: C.red, align: "center" });
  callout(s, "本报告所有层级优劣，主证据使用冻结日之后的纯前瞻收益。", 140, 538, 1000, 64, C.navy, C.white);
  note(s, "RESEARCH_SPEC_v0.1.json；SPEC_AMENDMENT_DERIVED_DEFINITIONS_20260921.json", "实现龙头明确标记 hindsight_descriptive=1。");
}

// 12 — forward returns
{
  const s = baseSlide("纯前瞻结果：Leader1 优于后排，但 Leader2/3 也有价值", "龙头表现", 12);
  const roles = ["Leader1", "Leader2", "Leader3", "ActiveRear"];
  const med = roles.map(r => leaderVal(r, "forward_terminal_return", "median") * 100);
  chartBar(s, {
    x: 76, y: 168, w: 720, h: 390, direction: "bar", max: 12, format: '0.0"%"',
    categories: ["Leader1", "Leader2", "Leader3", "后排"],
    series: [{ name: "冻结日后终值收益中位数", values: med, fill: C.red, valuesFormatCode: '0.0"%"' }], hasLegend: false,
  });
  const wins = roles.map(r => leaderVal(r, "forward_terminal_return", "positive_share"));
  const ns = roles.map(r => Number(leaderEvidence.find(x => x.initial_role === r && x.source_metric === "forward_terminal_return").n));
  const vals = [["角色", "样本 n", "正收益比例"], ...roles.map((r, i) => [r === "ActiveRear" ? "后排" : r, String(ns[i]), pct(wins[i])])];
  addTable(s, vals, 850, 188, 330, 268, [130, 90, 110], { bodySize: 14 });
  callout(s, "Leader3 的正收益比例最高（75.5%），提醒我们不能把“唯一龙头”当作已证明答案。", 850, 486, 330, 86, C.paleGold, C.navy);
  note(s, "LEADER_ROLE_EVIDENCE.csv", "forward_* 严格从冻结日之后开始；无后续交易日的角色不进入该指标分母。");
}

// 13 — drawdown
{
  const s = baseSlide("纯前瞻回撤：后排更容易出现深回撤", "龙头表现", 13);
  const roles = ["Leader1", "Leader2", "Leader3", "ActiveRear"];
  const dd = roles.map(r => Math.abs(leaderVal(r, "forward_max_drawdown", "median") * 100));
  chartBar(s, {
    x: 76, y: 168, w: 800, h: 400, direction: "bar", min: 0, max: 5, format: '0.0"%"',
    categories: ["Leader1", "Leader2", "Leader3", "后排"],
    series: [{ name: "最大回撤中位数", values: dd, fill: C.teal, valuesFormatCode: '0.0"%"' }], hasLegend: false,
    labelPosition: "outEnd",
  });
  kpi(s, "-1.7%", "Leader1 中位回撤", 924, 188, 250, C.teal, "冻结日之后");
  kpi(s, "-4.1%", "后排中位回撤", 924, 346, 250, C.red, "约为 Leader1 的 2.4 倍");
  callout(s, "核心股回撤通常较浅\n但退潮时仍可能大跌", 924, 510, 250, 80, C.paleRed, C.navy);
  note(s, "LEADER_ROLE_EVIDENCE.csv", "原始回撤保留负号；图中展示绝对幅度，数值越小代表路径回撤越浅。");
}

// 14 — realized origin
{
  const s = baseSlide("最终走出来的龙头：74.2% 已在冻结日前三", "实现龙头", 14);
  const chart = s.charts.add("doughnut", {
    position: { left: 70, top: 166, width: 590, height: 410 },
    categories: ["初始Leader1", "初始Leader2/3", "初始后排", "冻结日名单外"],
    series: [{ name: "407个冻结行情段", values: [229, 73, 43, 62], points: [
      { idx: 0, fill: C.red }, { idx: 1, fill: C.gold }, { idx: 2, fill: C.teal }, { idx: 3, fill: C.blue },
    ] }],
    doughnutOptions: { holeSize: 58 }, hasLegend: true,
    legend: { position: "bottom", overlay: false, textStyle: { fontSize: 12, fill: C.gray } },
    dataLabels: { showPercent: true, showCategoryName: false, position: "outEnd", textStyle: { fontSize: 12, bold: true, fill: C.ink } },
    chartFill: C.white, plotAreaFill: C.white, chartLine: { fill: "#E3DED4", width: 1 },
  });
  applyPresentationChartFont(chart, { fontFamily: family });
  kpi(s, "56.3%", "就是初始 Leader1", 736, 182, 420, C.red, "229 / 407");
  kpi(s, "74.2%", "位于初始前三", 736, 336, 420, C.gold, "302 / 407");
  callout(s, "仍有 10.6% 从后排走出，15.2% 甚至不在冻结日名单。", 736, 494, 420, 86, C.paleTeal, C.navy);
  note(s, "REALIZED_LEADER_EVIDENCE.csv", "实现龙头为后视镜标签。饼图四类互斥：Leader1、Leader2/3、ActiveRear、冻结名单外。");
}

// 15 — switching
{
  const s = baseSlide("龙头不是静态标签：行情内会换，退潮后更常换", "龙头换人", 15);
  rect(s, 76, 178, 520, 300, C.paleTeal, true);
  addText(s, "62.8%", 102, 210, 470, 92, { size: 62, bold: true, color: C.teal, align: "center" });
  addText(s, "同一行情段，相邻活跃日\n日度实现龙头发生切换", 108, 316, 458, 78, { size: 22, bold: true, color: C.navy, align: "center" });
  addText(s, "708 / 1,128", 108, 410, 458, 34, { size: 15, color: C.gray, align: "center" });
  rect(s, 684, 178, 520, 300, C.paleRed, true);
  addText(s, "88.3%", 710, 210, 470, 92, { size: 62, bold: true, color: C.red, align: "center" });
  addText(s, "同一原始题材退潮后再活跃\n代表龙头与上一段不同", 716, 316, 458, 78, { size: 22, bold: true, color: C.navy, align: "center" });
  addText(s, "579 / 656", 716, 410, 458, 34, { size: 15, color: C.gray, align: "center" });
  callout(s, "可用结论是“层级有用但需要动态更新”，不是“昨晚选出的龙头永远不变”。", 152, 530, 976, 66, C.navy, C.white);
  note(s, "REALIZED_LEADER_EVIDENCE.csv；LEADER_SWITCHES.csv", "切换基于日度/行情段实现龙头，属于后视镜描述，不能直接作为盘中信号准确率。");
}

// 16 — recurring theme leaders
{
  const s = baseSlide("退潮后换龙头：高频题材中是常态", "龙头换人", 16);
  const top = [...themeProfile].filter(r => Number(r.episode_count) > 1).sort((a, b) => Number(b.episode_count) - Number(a.episode_count) || a.primary_theme.localeCompare(b.primary_theme, "zh-CN")).slice(0, 9);
  const vals = [["题材", "行情段", "活跃日", "实现龙头数", "段间换人", "换人率"]];
  for (const r of top) vals.push([r.primary_theme, r.episode_count, r.active_days, r.distinct_realized_leader_count, r.after_fade_switches, pct(r.after_fade_switch_rate)]);
  addTable(s, vals, 76, 164, 900, 454, [210, 110, 110, 150, 120, 120], { bodySize: 13 });
  callout(s, "同一题材名称\n不等于同一轮行情", 1018, 188, 174, 96, C.paleGold, C.navy);
  addText(s, "新一段行情要重新识别：\n\n• 新分支\n• 新梯队\n• 新核心股\n• 是否仍有容量承接", 1018, 318, 180, 220, { size: 18, bold: true, color: C.charcoal });
  note(s, "THEME_PROFILE.csv", "表选取行情段数最多的9个原始题材；实现龙头和换人率均为后视镜描述。");
}

// 17 — capacity funnel
{
  const s = baseSlide("容量中军：先看覆盖率，再谈解释力", "容量中军", 17);
  addText(s, "793 个行情段", 92, 176, 290, 48, { size: 28, bold: true, color: C.navy, align: "center" });
  rect(s, 84, 232, 1110, 86, C.pale, true);
  addText(s, "有冻结日 407", 144, 250, 990, 48, { size: 28, bold: true, color: C.blue, align: "center" });
  rect(s, 220, 340, 838, 86, C.paleTeal, true);
  addText(s, "原始题材可精确映射概念成分 70", 250, 358, 778, 48, { size: 28, bold: true, color: C.teal, align: "center" });
  rect(s, 368, 448, 542, 86, C.paleRed, true);
  addText(s, "70 段全部找到明确中军", 398, 466, 482, 48, { size: 28, bold: true, color: C.red, align: "center" });
  callout(s, "关键限制：精确映射与“有明确中军”完全重合，无法用这批数据比较有中军 vs 无中军。", 154, 564, 972, 58, C.navy, C.white);
  note(s, "CAPACITY_EVIDENCE.csv；DATA_QUALITY_STAGE4_CAPACITY_LINKAGE.json", "386段无冻结日；337段有冻结日但原始题材无法精确映射；70段clear_core。");
}

// 18 — capacity states
{
  const s = baseSlide("中军状态与次日结果：没有单调关系", "容量中军", 18);
  chartBar(s, {
    x: 76, y: 166, w: 820, h: 410, direction: "column", max: 80, format: '0.0"%"', grouping: "clustered",
    categories: ["strong", "holding", "weak"],
    series: [
      { name: "次日题材仍活跃", values: [60.55, 52.0, 67.86], fill: C.red, valuesFormatCode: '0.0"%"' },
      { name: "冻结Leader1次日上涨", values: [58.72, 58.0, 54.76], fill: C.teal, valuesFormatCode: '0.0"%"' },
    ], hasLegend: true, labelPosition: "outEnd",
  });
  kpi(s, "67.9%", "weak 后题材仍活跃", 944, 182, 240, C.red, "反而高于 strong");
  kpi(s, "54.8%", "weak 后 Leader1 上涨", 944, 342, 240, C.teal, "略低，但差异不单调");
  callout(s, "结论：中军弱不能单独当退潮信号。", 944, 506, 240, 76, C.navy, C.white);
  note(s, "CAPACITY_EVIDENCE.csv", "仅70个精确映射样本；state-day分母按可得次日数据计；不做显著性或因果判断。");
}

// 19 — weak lag
{
  const s = baseSlide("出现首次中军偏弱后，行情通常仍有缓冲", "容量中军", 19);
  kpi(s, "32 / 70", "出现过 primary weak", 76, 174, 330, C.red, "45.7% 的可映射行情段");
  kpi(s, "2 天", "首次 weak 到行情结束中位数", 430, 174, 330, C.teal, "按交易日距离");
  kpi(s, "1—4 天", "四分位区间", 784, 174, 330, C.gold, "P25=1，P75=4");
  addText(s, "首次 weak", 100, 410, 150, 32, { size: 17, bold: true, color: C.red });
  rect(s, 238, 424, 790, 8, C.light, true);
  for (let i = 0; i <= 8; i++) {
    rect(s, 238 + i * 98.75, 414, 2, 28, i === 1 ? C.gold : i === 2 ? C.red : i === 4 ? C.teal : C.light);
    addText(s, String(i), 222 + i * 98.75, 450, 34, 26, { size: 13, color: C.gray, align: "center" });
  }
  pill(s, "P25 = 1", 296, 370, 110, C.gold, C.navy);
  pill(s, "中位 = 2", 416, 370, 124, C.red);
  pill(s, "P75 = 4", 640, 370, 110, C.teal);
  callout(s, "弱状态更像需要结合板块资金流继续观察的背景变量，而不是确定性退出按钮。", 136, 530, 1000, 64, C.paleGold, C.navy);
  note(s, "CAPACITY_EVIDENCE.csv", "first_primary_core_weak_lag：32个存在weak状态的行情段；均为精确映射样本。");
}

// 20 — YTD summary
{
  const s = baseSlide("2026 年度至今：龙头清单整体高度偏斜", "年度表", 20);
  const groupLabels = { all: "全部", initial_Leader1: "初始Leader1", realized_episode_leader: "实现龙头", both: "两者都是" };
  const rows = ytdSummary.filter(r => r.section === "group_summary");
  const vals = [["分组", "股票数", "中位收益", "正收益比例", "平均收益", "部分年度"]];
  for (const r of rows) vals.push([groupLabels[r.group], r.n, pct(r.median), pct(r.positive_share), pct(r.mean), r.partial_year_n]);
  addTable(s, vals, 76, 178, 860, 300, [190, 110, 150, 150, 140, 120], { bodySize: 14 });
  kpi(s, "678", "完整清单股票数", 976, 178, 218, C.navy, "初始Leader1 ∪ 实现龙头");
  kpi(s, "0.1%", "全表中位收益", 976, 334, 218, C.red, "正收益比例 50.0%");
  callout(s, "平均值 21.5% 远高于中位数，说明少数极端强股拉高均值。", 976, 492, 218, 96, C.paleGold, C.navy);
  note(s, "LEADER_YTD_SUMMARY.csv；LEADER_YTD_2026_FULL.csv", "收益由2026日涨跌幅复利至2026-08-31；3只部分年度样本已标记。");
}

// 21 — full table sample
{
  const s = baseSlide("完整年度表已交付：678 行、可排序、可筛选", "年度表", 21);
  const sample = ytdFull.slice(0, 12);
  const vals = [["代码", "名称", "身份", "YTD", "题材（截断）"]];
  for (const r of sample) {
    const role = Number(r.is_initial_leader1) && Number(r.is_realized_episode_leader) ? "初始+实现" : Number(r.is_initial_leader1) ? "初始L1" : "实现龙头";
    const rawYtdPct = Number(r.compounded_ytd_return_pct);
    const displayYtdPct = Math.abs(rawYtdPct) < 0.05 ? 0 : rawYtdPct;
    vals.push([r.ts_code, r.name, role, `${displayYtdPct.toFixed(1)}%`, r.themes.length > 18 ? r.themes.slice(0, 18) + "…" : r.themes]);
  }
  addTable(s, vals, 76, 156, 900, 486, [155, 150, 150, 120, 325], { headerSize: 12, bodySize: 11 });
  kpi(s, "678 行", "伴随 CSV", 1010, 174, 176, C.red, "完整文件见交付目录");
  addText(s, "字段包含：\n\n• 初始/实现龙头身份\n• 题材与行情段\n• 上市日期\n• 观测区间\n• 复利收益\n• 部分年度标记", 1010, 336, 180, 240, { size: 16, bold: true, color: C.charcoal });
  addText(s, "此处按代码顺序展示前12行，不是收益排名。", 1010, 586, 180, 48, { size: 12, color: C.gray });
  note(s, "LEADER_YTD_2026_FULL.csv", "PPT中仅展示表结构样例；完整678行CSV随PPT交付，避免把全表压缩成不可读小字。");
}

// 22 — conclusions
{
  const s = baseSlide("这轮研究真正建立了什么", "结论与下一步", 22, true);
  const items = [
    ["01", "题材会反复", "80/137 个原始题材退潮后再次活跃；但单段行情通常很短。"],
    ["02", "主线需要次日重验", "昨日第一仅 27% 次日仍第一；73% 的当日第一来自其他位置。"],
    ["03", "龙头层级有用但动态", "Leader1 纯前瞻优于后排；实现龙头仍会从后排/名单外产生，且换人频繁。"],
    ["04", "中军目前只是上下文", "70个可映射样本不呈单调关系；不能把中军弱单独当退潮信号。"],
  ];
  items.forEach((it, i) => {
    const y = 154 + i * 108;
    addText(s, it[0], 78, y, 70, 54, { size: 24, bold: true, color: C.gold });
    addText(s, it[1], 154, y, 270, 38, { size: 22, bold: true, color: C.white });
    addText(s, it[2], 430, y - 2, 748, 58, { size: 18, color: "#DCE3F0" });
    if (i < items.length - 1) rect(s, 154, y + 70, 1024, 1, "#4C5A74");
  });
  callout(s, "下一步仍先研究、后定义：用开盘30分钟的板块—资金流，验证前一晚主线与候选谁真正站住。", 154, 590, 1024, 62, C.red, C.white);
  note(s, "ANALYSIS_REPORT.md；Gemini独立审计结构化结果", "本页不构成交易策略。下一阶段需要另行冻结盘中资金流口径与验证问题。");
}

}

// v0.4 — rebuilt around the user's requested dragon-change questions.

// 1 — cover
{
  const s = presentation.slides.add();
  s.background.fill = C.navy;
  rect(s, 0, 0, 22, 720, C.red);
  pill(s, "研究报告 v0.2", 82, 70, 148, C.red);
  addText(s, "A股题材生命周期\n与龙位更替研究", 82, 146, 910, 190, { size: 54, bold: true, color: C.white });
  addText(s, "2025-10-30 至 2026-08-31｜开盘啦涨停题材｜描述性研究", 86, 356, 940, 40, { size: 20, color: "#DCE3F0" });
  rect(s, 86, 430, 1080, 1, "#52617D");
  addText(s, "研究题材如何活跃、退潮、再活跃，以及龙1、龙2、龙3在一段内和跨段如何更替。", 86, 464, 980, 86, { size: 26, bold: true, color: C.gold });
  addText(s, "205 个交易日 · 137 个活跃原始题材 · 793 段行情", 86, 624, 720, 30, { size: 15, color: "#B8C2D8" });
  addText(s, "01", 1144, 642, 64, 30, { size: 13, bold: true, color: C.gold, align: "right" });
  note(s, "SYNTHESIS_SUMMARY.json；SUMMARY_V02.json；RESEARCH_SPEC_v0.2_EXTENSION_20260921.json", "题材口径来自开盘啦，不是同花顺；本轮不使用新闻。无策略、无交易执行。" );
}

// 2 — research frame
{
  const s = baseSlide("先建立事实地图，再讨论交易规则", "研究框架", 2);
  const cols = [
    ["题材层", "活跃、退潮、反复\n每日前五与交接", C.paleRed],
    ["龙位层", "龙1/2/3与后排\n段内、跨段换位", C.paleGold],
    ["容量层", "中军识别与状态\n作为背景变量", C.paleTeal],
    ["收益层", "纯前瞻、区间路径\n极端边界", C.pale],
  ];
  cols.forEach((d, i) => {
    const x = 76 + i * 280;
    rect(s, x, 182, 250, 150, d[2], true);
    addText(s, d[0], x + 18, 200, 214, 38, { size: 24, bold: true, color: C.navy });
    addText(s, d[1], x + 18, 250, 214, 62, { size: 16, color: C.charcoal });
  });
  callout(s, "核心龙骨：板块 → 资金流 → 个股。当前报告只回答历史结构，不把描述性结果直接变成买卖信号。", 76, 374, 1120, 72, C.navy, C.white);
  addText(s, "本轮新增的两个专题", 76, 482, 250, 32, { size: 17, bold: true, color: C.red });
  addText(s, "① 反复题材：退潮后旧龙还在不在、新龙从哪里来\n② 单轮多日：从 T 日起，龙1能否守位、龙2/3能否接力", 76, 524, 1080, 82, { size: 23, bold: true, color: C.ink });
  note(s, "MODIFICATION_FRAMEWORK_v0.2.md；RESEARCH_SPEC_v0.2_EXTENSION_20260921.json", "研究顺序由用户要求冻结。" );
}

// 3 — data benchmark
{
  const s = baseSlide("数据基准：开盘啦题材，2025-10-30 至 2026-08-31", "研究边界", 3);
  kpi(s, "205", "交易日", 76, 156, 250, C.red, "逐日涨停/炸板题材");
  kpi(s, "19,716", "合格记录", 346, 156, 250, C.teal, "SH/SZ；排除北交所与ST前缀");
  kpi(s, "137", "活跃原始题材", 616, 156, 250, C.blue, "题材标签不主动归并");
  kpi(s, "793", "行情段", 886, 156, 250, C.gold, "退潮后再活跃视为新段");
  const vals = [
    ["对象", "冻结口径", "限制"],
    ["题材", "开盘啦 lu_desc 原始标签", "不是同花顺；未做新闻归因"],
    ["活跃日", "≥3个封板；或≥2个且至少1个连板", "高度不能单独压过宽度"],
    ["行情段", "退潮后重新活跃即新一段", "同名题材可有多段、不同催化"],
    ["个股", "SH/SZ、排除ST/异常交易所", "角色依赖当日题材精确映射"],
  ];
  addTable(s, vals, 76, 324, 1120, 270, [170, 500, 450], { bodySize: 13 });
  note(s, "RESEARCH_SPEC_v0.1.json；RESEARCH_SPEC_v0.2_EXTENSION_20260921.json；DATA_QUALITY_V02.json", "所有新增口径在新增结果检视前冻结；原始SQLite只读。" );
}

// 4 — clocks and ranking
{
  const s = baseSlide("三个时间钟：龙位必须说明“在哪一天认定”", "龙位定义", 4);
  const vals = [
    ["时间钟", "用于回答", "角色生成"],
    ["冻结龙位", "当时能否识别、后续表现如何", "首次进入展示集合日"],
    ["段首龙位", "反复题材跨段是否换龙", "每段开始日"],
    ["当日龙位", "单轮多日 t−T 如何换位", "每个交易日重排"],
  ];
  addTable(s, vals, 76, 162, 610, 228, [150, 260, 200], { bodySize: 13 });
  rect(s, 724, 162, 472, 228, C.paleTeal, true);
  addText(s, "龙1（龙头）/ 龙2 / 龙3排序", 748, 176, 430, 34, { size: 21, bold: true, color: C.navy });
  addText(s, "1. 连板高度降序（缺失最后）\n2. 封住涨停优先于炸板\n3. 封板时间升序（缺失最后）\n4. 封单金额降序\n5. 成交额降序\n6. 股票代码升序", 748, 220, 420, 154, { size: 16, color: C.charcoal });
  rect(s, 76, 430, 1120, 146, C.paleRed, true);
  addText(s, "“实现龙头”是什么", 98, 444, 250, 32, { size: 20, bold: true, color: C.red });
  addText(s, "完整行情段结束后，按最高连板 → 段内累计峰值收益 → 最早达到最高板日期 → 当日最早封板 → 股票代码选出的后视镜代表股。它用于解释“最后谁走出来”，不替换当时冻结的龙位。", 98, 484, 1070, 72, { size: 17, color: C.charcoal });
  note(s, "RESEARCH_SPEC_v0.2_EXTENSION_20260921.json", "所有并列打破顺序均为冻结确定性规则。" );
}

// 5 — opportunity universe
{
  const s = baseSlide("机会母体：多数活跃题材会回来，但不是连续上涨", "题材母体", 5);
  chartBar(s, { x: 76, y: 168, w: 720, h: 390, direction: "bar", categories: ["反复活跃", "单轮多日", "仅活跃1日"], series: [{ name: "原始题材数", values: [80, 10, 47], fill: C.red }], hasLegend: false });
  kpi(s, "80 / 137", "退潮后再次活跃", 846, 178, 320, C.red, "58.4% 的活跃题材");
  kpi(s, "736", "反复题材行情段", 846, 334, 320, C.teal, "跨段换龙分析母体");
  callout(s, "同一个大题材名称，不等于同一轮行情。", 846, 492, 320, 84, C.paleGold, C.navy);
  note(s, "THEME_PROFILE.csv；SUMMARY_V02.json", "反复活跃以同一开盘啦原始标签退潮后再次成段定义。" );
}

// 6 — intensity with explicit axes
{
  const s = baseSlide("反复题材更深：宽度与高度的类别中位数", "生命周期", 6);
  chartBar(s, { x: 76, y: 174, w: 540, h: 330, direction: "column", max: 10, categories: ["反复活跃", "单轮多日", "一日型"], series: [{ name: "峰值涨停家数", values: [8, 7, 3], fill: C.red }], hasLegend: false });
  chartBar(s, { x: 664, y: 174, w: 540, h: 330, direction: "column", max: 6, categories: ["反复活跃", "单轮多日", "一日型"], series: [{ name: "峰值最高连板", values: [5, 3, 2], fill: C.teal }], hasLegend: false });
  addText(s, "左纵轴：每个题材峰值涨停家数的类别中位数（家）", 86, 510, 520, 28, { size: 13, bold: true, color: C.red, align: "center" });
  addText(s, "右纵轴：每个题材峰值最高连板的类别中位数（板）", 674, 510, 520, 28, { size: 13, bold: true, color: C.teal, align: "center" });
  callout(s, "这说明反复题材通常更有梯队深度；不等于每一段都能持续，也不等于可直接交易。", 76, 556, 1128, 56, C.navy, C.white);
  note(s, "LIFECYCLE_COHORT_SUMMARY.csv", "先在题材层求每个题材的峰值，再在生命周期类别内取中位数。" );
}

// 7 — combined theme rotation
{
  const s = baseSlide("题材交接：旧第一会降级，新第一常从候选或场外跃迁", "次日换位", 7);
  chartBar(s, { x: 76, y: 174, w: 530, h: 340, direction: "bar", max: 50, categories: ["仍是第一", "降至第2—5", "退出前五"], series: [{ name: "昨日第一的次日去向", values: [26.96, 41.18, 31.86], fill: C.red }], hasLegend: false });
  chartBar(s, { x: 674, y: 174, w: 530, h: 340, direction: "bar", max: 45, categories: ["昨日第一", "昨日第2—5", "昨日前五外"], series: [{ name: "当日第一的来源", values: [26.96, 37.75, 35.29], fill: C.teal }], hasLegend: false });
  addText(s, "昨日第一的次日命运", 178, 522, 330, 28, { size: 16, bold: true, color: C.red, align: "center" });
  addText(s, "当日第一来自哪里", 776, 522, 330, 28, { size: 16, bold: true, color: C.teal, align: "center" });
  callout(s, "含义：前一晚要保留主线、次主线与新方向候选；次日必须重验资金选择。", 176, 570, 928, 52, C.paleGold, C.navy);
  note(s, "ROTATION_SUMMARY.csv；DAILY_THEME_HIERARCHY.csv", "分母=204组相邻交易日；百分比按未四舍五入计数闭合。" );
}

// 8 — role separation
{
  const s = baseSlide("当时选出的龙，与最后走出的龙，是两套标签", "个股层级", 8);
  rect(s, 76, 176, 500, 310, C.paleTeal, true);
  addText(s, "冻结龙位｜可前瞻", 104, 198, 440, 42, { size: 25, bold: true, color: C.navy });
  addText(s, "在可见日按固定排序得到龙1/2/3。\n\n用于检验：\n• 选出来之后的终值收益\n• 最大回撤\n• 是否后来成为实现龙头", 104, 254, 430, 200, { size: 18, color: C.charcoal });
  rect(s, 704, 176, 500, 310, C.paleRed, true);
  addText(s, "实现龙头｜后视镜", 732, 198, 440, 42, { size: 25, bold: true, color: C.navy });
  addText(s, "看完整行情段后按表现选出。\n\n用于解释：\n• 最终是谁走出来\n• 它在冻结日是什么身份\n• 是否从后排或名单外晋级", 732, 254, 430, 200, { size: 18, color: C.charcoal });
  addText(s, "≠", 592, 292, 96, 60, { size: 42, bold: true, color: C.red, align: "center" });
  callout(s, "收益比较的主证据使用冻结日之后的纯前瞻窗口。", 140, 536, 1000, 64, C.navy, C.white);
  note(s, "RESEARCH_SPEC_v0.1.json；RESEARCH_SPEC_v0.2_EXTENSION_20260921.json", "实现龙头始终标记为 hindsight_descriptive。" );
}

// 9 — forward return
{
  const s = baseSlide("纯前瞻：龙1终值收益最高，龙2/3仍显著好于后排", "龙位表现", 9);
  const roles = ["Leader1", "Leader2", "Leader3", "ActiveRear"];
  const med = roles.map(r => leaderVal(r, "forward_terminal_return", "median") * 100);
  chartBar(s, { x: 76, y: 168, w: 720, h: 390, direction: "bar", max: 12, categories: ["龙1（龙头）", "龙2", "龙3", "后排"], series: [{ name: "冻结日后终值收益中位数", values: med, fill: C.red }], hasLegend: false });
  const vals = [["角色", "样本 n", "正收益比例"], ...roles.map(r => { const row = leaderEvidence.find(x => x.initial_role === r && x.source_metric === "forward_terminal_return"); return [r === "Leader1" ? "龙1" : r === "Leader2" ? "龙2" : r === "Leader3" ? "龙3" : "后排", row.n, pct(row.positive_share)]; })];
  addTable(s, vals, 850, 188, 330, 268, [130, 90, 110], { bodySize: 14 });
  callout(s, "龙3正收益比例最高（75.5%）：前三是层级，不应过早压缩成“唯一一只”。", 850, 486, 330, 92, C.paleGold, C.navy);
  note(s, "LEADER_ROLE_EVIDENCE.csv", "forward_* 严格从冻结日之后开始；无后续交易日不进入分母。" );
}

// 10 — drawdown
{
  const s = baseSlide("纯前瞻：后排路径更脆，回撤约为龙1的2.4倍", "龙位表现", 10);
  const roles = ["Leader1", "Leader2", "Leader3", "ActiveRear"];
  const dd = roles.map(r => Math.abs(leaderVal(r, "forward_max_drawdown", "median") * 100));
  chartBar(s, { x: 76, y: 168, w: 800, h: 400, direction: "bar", min: 0, max: 5, categories: ["龙1（龙头）", "龙2", "龙3", "后排"], series: [{ name: "最大回撤中位数（绝对值）", values: dd, fill: C.teal }], hasLegend: false });
  kpi(s, "−1.7%", "龙1中位回撤", 924, 188, 250, C.teal, "冻结日之后");
  kpi(s, "−4.1%", "后排中位回撤", 924, 346, 250, C.red, "路径更容易断裂");
  callout(s, "龙位更强不代表免疫退潮。", 924, 510, 250, 80, C.paleRed, C.navy);
  note(s, "LEADER_ROLE_EVIDENCE.csv", "图中展示回撤绝对幅度；越小越浅。" );
}

// 11 — realized origin
{
  const s = baseSlide("最终走出来的实现龙头：74.2% 已在冻结日前三", "实现龙头", 11);
  const chart = s.charts.add("doughnut", { position: { left: 70, top: 166, width: 590, height: 410 }, categories: ["冻结龙1", "冻结龙2/3", "冻结后排", "冻结名单外"], series: [{ name: "407个冻结行情段", values: [229, 73, 43, 62], points: [{ idx: 0, fill: C.red }, { idx: 1, fill: C.gold }, { idx: 2, fill: C.teal }, { idx: 3, fill: C.blue }] }], doughnutOptions: { holeSize: 58 }, hasLegend: true, legend: { position: "bottom", overlay: false, textStyle: { fontSize: 12, fill: C.gray } }, dataLabels: { showPercent: true, position: "outEnd", textStyle: { fontSize: 12, bold: true, fill: C.ink } }, chartFill: C.white, plotAreaFill: C.white, chartLine: { fill: "#E3DED4", width: 1 } });
  applyPresentationChartFont(chart, { fontFamily: family });
  kpi(s, "56.3%", "就是冻结龙1", 736, 182, 420, C.red, "229 / 407");
  kpi(s, "74.2%", "位于冻结前三", 736, 336, 420, C.gold, "302 / 407");
  callout(s, "仍有25.8%从后排或冻结名单外走出。", 736, 494, 420, 86, C.paleTeal, C.navy);
  note(s, "REALIZED_LEADER_EVIDENCE.csv", "实现龙头为后视镜标签；四类互斥。" );
}

// 12 — recurrent divider
{
  const s = baseSlide("专题一｜退潮后再活跃，龙会不会换？", "反复题材", 12, true);
  addText(s, "跨行情段看三件事", 82, 170, 450, 40, { size: 24, bold: true, color: C.gold });
  addText(s, "① 前一段的龙1/2/3，下一段还能不能进入前三\n② 下一段前三里有多少是新股票\n③ 跨两段以上持续出现的，是龙1还是龙2/3", 82, 230, 1040, 180, { size: 30, bold: true, color: C.white });
  callout(s, "母体：80个反复题材、736段行情；跨段龙位统一使用段首龙位。", 82, 496, 1040, 70, C.red, C.white);
  note(s, "RECURRENT_THEME_DRAGON_DETAIL.csv；RECURRENT_THEME_DRAGON_SUMMARY.csv", "本专题不把冻结日晚于段首的角色强行补到段首。" );
}

// 13 — recurrent headline
{
  const s = baseSlide("跨段换龙是主旋律：后续前三槽位中67.8%是新龙", "反复题材", 13);
  kpi(s, "67.8%", "后续前三槽位为新股票", 76, 172, 330, C.red, "1,257 / 1,854");
  kpi(s, "15.2%", "与紧邻上一段连续", 430, 172, 330, C.teal, "281 / 1,854");
  kpi(s, "32.2%", "曾在任一前段出现", 784, 172, 330, C.gold, "597 / 1,854");
  rect(s, 76, 352, 1038, 134, C.pale, true);
  addText(s, "另一种分母：按“题材×股票”去重", 98, 370, 390, 30, { size: 18, bold: true, color: C.navy });
  addText(s, "1,480 个曾进入段首前三的题材—股票组合中，375 个至少跨两段再次进入前三；龙持续率 = 25.3%。", 98, 414, 982, 52, { size: 21, bold: true, color: C.charcoal });
  callout(s, "旧龙不是完全失效，但“旧龙自动延续”不是多数情况。", 164, 536, 950, 62, C.navy, C.white);
  note(s, "SUMMARY_V02.json", "槽位分母与去重股票分母不同，不能混用。" );
}

// 14 — persistent role types
{
  const s = baseSlide("持续两段以上的375只龙：多数会换位，而非一直守龙1", "反复题材", 14);
  chartBar(s, { x: 76, y: 174, w: 720, h: 370, direction: "bar", max: 210, categories: ["始终是龙1", "做过龙1且发生换位", "始终只在龙2/3"], series: [{ name: "题材—股票组合数", values: [64, 181, 130], fill: C.teal }], hasLegend: false });
  kpi(s, "64", "始终守龙1", 850, 176, 330, C.red, "17.1% 的持续龙");
  kpi(s, "181", "做过龙1且换位", 850, 330, 330, C.gold, "48.3% 的持续龙");
  callout(s, "龙1是相对位置；题材重新活跃时，龙2/3可能晋级，龙1也可能退位。", 850, 486, 330, 94, C.paleRed, C.navy);
  note(s, "RECURRENT_THEME_DRAGON_SUMMARY.csv；SUMMARY_V02.json", "分类基于各题材内同一股票跨段的段首龙位路径。" );
}

// 15–16 — representative recurrent tables
{
  const top = [...recurrentSummary].sort((a, b) => Number(b.episode_count) - Number(a.episode_count) || a.primary_theme.localeCompare(b.primary_theme, "zh-CN")).slice(0, 12);
  for (let page = 0; page < 2; page++) {
    const s = baseSlide(page === 0 ? "代表性反复题材：前三段日期与龙1/2/3（上）" : "代表性反复题材：前三段日期与龙1/2/3（下）", "跨段明细", 15 + page);
    const vals = [["题材", "段数", "第1段", "第2段", "第3段", "龙持续率"]];
    for (const r of top.slice(page * 6, page * 6 + 6)) {
      const ep = idx => `${r[`episode${idx}_start_date`]}\n${namesFromTop3(r[`episode${idx}_segment_start_top3`])}`;
      vals.push([r.primary_theme, r.episode_count, ep(1), ep(2), ep(3), pct(r.persistent_stock_rate)]);
    }
    addTable(s, vals, 54, 150, 1172, 486, [126, 58, 280, 280, 280, 148], { headerSize: 11, bodySize: 9 });
    note(s, "RECURRENT_THEME_DRAGON_SUMMARY.csv", "按行情段数降序选12个代表题材；完整80题材与全部段明细随附CSV。龙持续率=该题材内至少跨两段进入前三的去重股票数/曾进入前三的去重股票数。" );
  }
}

// 17 — prior dragon next episode
{
  const s = baseSlide("旧龙到下一段：龙1保留率最高，但大多数仍会退出前三", "跨段表现", 17);
  const rows = priorDragonSummary.filter(r => ["Dragon1", "Dragon2", "Dragon3"].includes(r.prior_role));
  const vals = [["前段角色", "下一段仍前三", "下一段仍同位", "下一段成为龙1", "下一段收益中位", "正收益比例"]];
  for (const r of rows) vals.push([r.prior_role === "Dragon1" ? "龙1" : r.prior_role === "Dragon2" ? "龙2" : "龙3", pct(r.later_top3_share), pct(r.later_same_role_share), pct(r.later_dragon1_share), pctPoint(r.period_compounded_return_median), pct(r.positive_return_share)]);
  addTable(s, vals, 76, 174, 1120, 244, [150, 180, 180, 180, 210, 180], { bodySize: 15 });
  kpi(s, "19.5%", "旧龙1下一段仍前三", 76, 462, 330, C.red, "龙2 15.4%｜龙3 9.7%");
  kpi(s, "+2.8%", "旧龙1下一段收益中位", 430, 462, 330, C.teal, "仅描述后来那一段路径");
  callout(s, "正收益不等于可买：后段通常很短，且这里不是盘中可执行回测。", 784, 462, 330, 132, C.paleGold, C.navy);
  note(s, "RECURRENT_PRIOR_DRAGON_LATER_SUMMARY.csv", "收益窗口=下一行情段；角色分母与收益分母均逐行标明。" );
}

// 18 — single-wave divider
{
  const s = baseSlide("专题二｜同一段行情里，龙位如何随 t−T 变化？", "单轮多日", 18, true);
  addText(s, "T = 行情段开始日", 82, 176, 520, 42, { size: 27, bold: true, color: C.gold });
  addText(s, "以10个“只出现一段、但持续多日”的题材为母体：\n逐交易日重新排序龙1/2/3，观察守位、接力与新龙进入。", 82, 244, 1030, 130, { size: 30, bold: true, color: C.white });
  callout(s, "样本在T+2后快速变小；每个百分比必须和n一起看。", 82, 486, 920, 70, C.red, C.white);
  note(s, "SINGLE_WAVE_MULTIDAY_DRAGON_DAILY.csv；SINGLE_WAVE_MULTIDAY_DRAGON_AGGREGATE.csv", "主时间轴 tau_trade；tau_active仅作复核。" );
}

// 19 — t-T aggregate
{
  const s = baseSlide("T+1：龙1有40%守住龙1，前三槽位已有60%换成新股票", "段内换位", 19);
  const agg = singleWaveAggregate.filter(r => r.time_axis === "tau_trade" && [0,1,2].includes(Number(r.tau_index)));
  const vals = [["t−T", "题材 n", "T日龙1仍龙1", "T日龙1仍前三", "T日龙2/3仍前三", "新股票占当日前三"]];
  for (const r of agg) vals.push([`T+${r.tau_index}`, r.episode_denominator, pct(r.t_dragon1_stays_dragon1_share), pct(r.t_dragon1_stays_top3_share), pct(r.t_dragon23_retained_share), pct(r.new_daily_top3_share)]);
  addTable(s, vals, 76, 170, 1120, 250, [120, 120, 210, 210, 230, 230], { bodySize: 14 });
  kpi(s, "40.0%", "T+1龙1仍是龙1", 76, 458, 330, C.red, "4 / 10");
  kpi(s, "21.1%", "T+1龙2/3仍在前三", 430, 458, 330, C.teal, "4 / 19个可用槽位");
  callout(s, "T+2仅剩3个题材，不做总体结论。", 784, 458, 330, 132, C.paleGold, C.navy);
  note(s, "SINGLE_WAVE_MULTIDAY_DRAGON_AGGREGATE.csv", "T日为基准，T+1起对同一批T日龙位跟踪；空缺槽位不计入可用分母。" );
}

// 20 — short cases
{
  const s = baseSlide("九个短案例：守位、晋级和整组替换都存在", "段内案例", 20);
  const themes = singleWaveCases.filter(r => r.primary_theme !== "一季报增长").slice(0, 9);
  const vals = [["题材", "T日龙1/2/3", "T+1龙1/2/3", "观察"]];
  for (const c of themes) {
    const next = singleWaveDaily.find(r => r.primary_theme === c.primary_theme && Number(r.tau_trade) === 1);
    const t = namesFromTop3(c.t_top3).replaceAll(" / ", "\n");
    const n = next ? `龙1 ${next.dragon1_name || "—"}\n龙2 ${next.dragon2_name || "—"}\n龙3 ${next.dragon3_name || "—"}` : "—";
    const firstT = c.t_top3.match(/Dragon1=[^:]*:([^;]+)/)?.[1] ?? "";
    const obs = next && next.dragon1_name === firstT ? "龙1守位" : next && [next.dragon2_name, next.dragon3_name].includes(firstT) ? "龙1退位仍在前三" : "龙1退出/无后续";
    vals.push([c.primary_theme, t, n, obs]);
  }
  addTable(s, vals, 52, 144, 1174, 504, [140, 330, 330, 374], { headerSize: 11, bodySize: 9 });
  note(s, "SINGLE_WAVE_MULTIDAY_DRAGON_CASES.csv；SINGLE_WAVE_MULTIDAY_DRAGON_DAILY.csv", "九个短案例为全部单轮多日题材中除一季报增长外的案例；一季报增长单列下一页。" );
}

// 21 — long case
{
  const s = baseSlide("一季报增长：长段行情中，段首龙位很快被多次替换", "长案例", 21);
  const rows = singleWaveDaily.filter(r => r.primary_theme === "一季报增长").sort((a,b) => Number(a.tau_trade)-Number(b.tau_trade)).slice(0, 8);
  const vals = [["日期", "t−T", "龙1", "龙2", "龙3", "是否活跃"]];
  for (const r of rows) vals.push([r.trade_date, `T+${r.tau_trade}`, r.dragon1_name || "—", r.dragon2_name || "—", r.dragon3_name || "—", Number(r.is_active) ? "是" : "否"]);
  addTable(s, vals, 76, 154, 800, 470, [150, 90, 170, 170, 170, 100], { bodySize: 11 });
  const c = singleWaveCases.find(r => r.primary_theme === "一季报增长");
  callout(s, "段首前三", 920, 174, 250, 46, C.paleGold, C.navy);
  addText(s, namesFromTop3(c?.t_top3 ?? "").replaceAll(" / ", "\n"), 920, 232, 250, 124, { size: 16, bold: true, color: C.charcoal });
  callout(s, "冻结日前三", 920, 384, 250, 46, C.paleTeal, C.navy);
  addText(s, namesFromTop3(c?.frozen_top3 ?? "").replaceAll(" / ", "\n"), 920, 442, 250, 124, { size: 16, bold: true, color: C.charcoal });
  note(s, "SINGLE_WAVE_MULTIDAY_DRAGON_DAILY.csv；SINGLE_WAVE_MULTIDAY_DRAGON_CASES.csv", "该题材持续18个交易日；页面只展示前8日。段首龙位与更晚冻结日龙位本来就可能不同。" );
}

// 22 — capacity criteria
{
  const s = baseSlide("容量中军怎么选：候选门槛、评分与覆盖率", "容量中军", 22);
  rect(s, 76, 158, 520, 366, C.paleTeal, true);
  addText(s, "选择依据", 100, 176, 460, 38, { size: 24, bold: true, color: C.navy });
  addText(s, "候选范围：冻结日同题材精确成分，SH/SZ，排除ST/异常交易所\n\n门槛：\n• 自由流通市值分位 ≥ 80%\n• 成交额分位 ≥ 70%\n• 三项字段完整\n\n评分：0.55×市值分位 + 0.35×成交额分位 + 0.10×涨幅分位\n取第1名为主中军，第2名为备选。", 100, 224, 460, 282, { size: 16, color: C.charcoal });
  addText(s, "793 段", 708, 174, 330, 42, { size: 27, bold: true, color: C.navy, align: "center" });
  rect(s, 668, 230, 420, 72, C.pale, true); addText(s, "有冻结日 407", 688, 245, 380, 40, { size: 24, bold: true, color: C.blue, align: "center" });
  rect(s, 730, 326, 296, 72, C.paleGold, true); addText(s, "精确映射 70", 750, 341, 256, 40, { size: 24, bold: true, color: C.gold, align: "center" });
  rect(s, 782, 422, 192, 72, C.paleRed, true); addText(s, "明确中军 70", 792, 437, 172, 40, { size: 21, bold: true, color: C.red, align: "center" });
  callout(s, "映射与中军覆盖完全重合，不能比较“有中军 vs 无中军”。", 652, 544, 460, 62, C.navy, C.white);
  note(s, "CAPACITY_EVIDENCE.csv；DATA_QUALITY_STAGE4_CAPACITY_LINKAGE.json；RESEARCH_SPEC_v0.2_EXTENSION_20260921.json", "题材—成分精确映射是当前主要限制。" );
}

// 23 — capacity results
{
  const s = baseSlide("中军状态暂时不能单独解释题材延续", "容量中军", 23);
  chartBar(s, { x: 76, y: 166, w: 790, h: 410, direction: "column", max: 80, categories: ["strong", "holding", "weak"], series: [{ name: "次日题材仍活跃", values: [60.55, 52.0, 67.86], fill: C.red }, { name: "冻结龙1次日上涨", values: [58.72, 58.0, 54.76], fill: C.teal }], hasLegend: true });
  kpi(s, "32 / 70", "出现过主中军weak", 914, 178, 270, C.red, "45.7%");
  kpi(s, "2天", "首次weak到段尾中位", 914, 334, 270, C.teal, "P25=1｜P75=4");
  callout(s, "weak后题材仍活跃67.9%，不呈单调关系。", 914, 490, 270, 94, C.paleGold, C.navy);
  note(s, "CAPACITY_EVIDENCE.csv", "仅70个精确映射样本；中军弱更适合作为与板块资金流组合的背景变量。" );
}

// 24 — return definitions
{
  const s = baseSlide("678只龙股的四个收益口径：可比，但含义完全不同", "收益边界", 24);
  const vals = [
    ["指标", "窗口/算法", "正确解读"],
    ["2026 YTD", "2026首个可得交易日至2026-08-31日收益复利", "年度至今，不等于研究区间"],
    ["研究区间收益", "2025-10-30至2026-08-31日收益复利", "完整研究窗口；上市晚则从可得日开始"],
    ["最低→后续最高", "复权日线最低价后出现的最高价", "神抄底边界；同日顺序不可知"],
    ["最高→后续最低", "复权日线最高价后出现的最低价", "山顶买入边界；路径极端风险"],
  ];
  addTable(s, vals, 76, 170, 1120, 300, [210, 520, 390], { bodySize: 13 });
  callout(s, "后两项是日线极端边界，不是可实现回测收益；不得用于宣称策略表现。", 76, 508, 1120, 72, C.paleRed, C.navy);
  addText(s, "完整678行表包含股票、身份、题材、行情段、观测区间、缺失标记和四个收益字段。", 96, 594, 1080, 30, { size: 16, bold: true, color: C.charcoal });
  note(s, "LEADER_RETURNS_EXTENDED.csv；SUMMARY_V02.json", "日线极值采用复权OHLC；678只全部有可用OHLC，缺失日=0。" );
}

// 25 — return summary
{
  const s = baseSlide("研究区间收益中位数+7.1%，但极端路径同时很大", "收益汇总", 25);
  kpi(s, "+7.1%", "研究区间收益中位", 76, 174, 330, C.red, "正收益比例58.6%");
  kpi(s, "+97.6%", "最低→后续最高中位", 430, 174, 330, C.teal, "神抄底上界");
  kpi(s, "−53.4%", "最高→后续最低中位", 784, 174, 330, C.gold, "山顶买入下界");
  const vals = [
    ["指标", "P25", "中位数", "均值", "P75"],
    ["研究区间收益", "−14.0%", "+7.1%", "+30.7%", "+48.0%"],
    ["最低→后续最高", "+61.1%", "+97.6%", "+142.0%", "+163.2%"],
    ["最高→后续最低", "−59.8%", "−53.4%", "−52.6%", "−46.3%"],
  ];
  addTable(s, vals, 126, 370, 1020, 210, [260, 190, 190, 190, 190], { bodySize: 14 });
  note(s, "SUMMARY_V02.json；LEADER_RETURNS_EXTENDED.csv", "均值受极端强股影响；解释优先看中位数与四分位。" );
}

// 26 — sample table
{
  const s = baseSlide("678只龙股明细：四个收益口径并列交付", "龙股清单", 26);
  const sorted = [...leaderReturnsExtended].sort((a,b) => Number(b.period_compounded_return_pct)-Number(a.period_compounded_return_pct));
  const sample = [...sorted.slice(0, 5), ...sorted.slice(-5)];
  const vals = [["股票", "身份", "2026 YTD", "研究区间", "最低→后续最高", "最高→后续最低"]];
  for (const r of sample) {
    const role = Number(r.is_initial_leader1) && Number(r.is_realized_episode_leader) ? "龙1+实现" : Number(r.is_initial_leader1) ? "龙1" : "实现龙头";
    vals.push([`${r.name}\n${r.ts_code}`, role, pctPoint(Number(r.compounded_ytd_return) * 100), pctPoint(r.period_compounded_return_pct), pctPoint(r.ideal_low_to_later_high_pct), pctPoint(r.worst_high_to_later_low_pct)]);
  }
  addTable(s, vals, 54, 146, 1172, 500, [190, 150, 180, 190, 230, 232], { headerSize: 11, bodySize: 10 });
  note(s, "LEADER_RETURNS_EXTENDED.csv", "示例按研究区间收益取最高5只与最低5只，仅用于展示字段与极端跨度；完整678行CSV可排序筛选。" );
}

// 27 — conclusion
{
  const s = baseSlide("这轮研究建立的五个事实", "结论与下一步", 27, true);
  const items = [
    ["01", "题材会反复", "80/137个活跃题材退潮后重新成段，但同名不等于同一轮。"],
    ["02", "主线需重验", "昨日第一仅27%次日仍第一；新第一常从次主线或前五外产生。"],
    ["03", "龙位有层级", "冻结龙1收益与回撤优于后排；龙2/3也提供有效候选。"],
    ["04", "跨段多换龙", "后续前三槽位67.8%是新股票；持续龙中多数发生角色移动。"],
    ["05", "中军仍是上下文", "选择规则已明确，但70个可映射样本不支持单调延续关系。"],
  ];
  items.forEach((it, i) => {
    const y = 132 + i * 96;
    addText(s, it[0], 78, y, 70, 48, { size: 22, bold: true, color: C.gold });
    addText(s, it[1], 154, y, 250, 38, { size: 21, bold: true, color: C.white });
    addText(s, it[2], 414, y - 2, 764, 54, { size: 17, color: "#DCE3F0" });
    if (i < items.length - 1) rect(s, 154, y + 64, 1024, 1, "#4C5A74");
  });
  callout(s, "下一步仍先定义、再验证：把开盘30分钟板块—资金流接到这套题材与龙位事实地图上。", 154, 614, 1024, 52, C.red, C.white);
  note(s, "SUMMARY_V02.json；本报告全部伴随CSV", "本页不构成交易建议。" );
}

await fs.mkdir(buildDir, { recursive: true });
await fs.mkdir(outputDir, { recursive: true });
const stagingDir = path.join(buildDir, ".codex-finalizer");
await fs.mkdir(stagingDir, { recursive: true });
const candidatePath = path.join(stagingDir, "candidate_v0.2.pptx");
await (await PresentationFile.exportPptx(presentation)).save(candidatePath);

const requirements = {
  explicitTotalSlideCount: 27,
  requiredNativeTableOwnerSlides: [3, 4, 9, 15, 16, 17, 19, 20, 21, 24, 25, 26],
  requiredNativeChartOwnerSlides: [5, 6, 7, 9, 10, 11, 14, 23],
  requiredEmbeddedWorkbookChartOwnerSlides: [],
  materializeLiteralChartWorkbooks: true,
  tableArithmeticContracts: [],
};
const fontPolicy = { basis: "design", families: [family], scriptFonts: { ea: family } };
const result = await finalizePresentation({
  ...requirements,
  workspaceDir,
  candidatePath,
  finalPath: FINAL_PPTX,
  pythonExecutable: RUNTIME_PYTHON,
  integrityValidatorPath: path.join(SKILL_DIR, "container_tools/inspect_presentation_package_integrity.py"),
  layoutValidatorPath: path.join(SKILL_DIR, "container_tools/inspect_presentation_layout_geometry.py"),
  layoutArgs: [
    "--expected-slide-size-emu", "12192000,6858000",
    "--validate-bullet-geometry", "--validate-heading-fit",
    ...requirements.requiredNativeTableOwnerSlides.flatMap(n => ["--require-native-table-slide", String(n)]),
  ],
  requiredNativeTableOwnerSlides: requirements.requiredNativeTableOwnerSlides,
  requiredNativeChartOwnerSlides: requirements.requiredNativeChartOwnerSlides,
  fontPolicy,
  verifyArtifactToolImport: true,
  receiptPath: path.join(stagingDir, "A股题材生命周期与龙位更替研究_v0.2.validation.json"),
});
console.log(JSON.stringify({ finalPath: FINAL_PPTX, candidatePath, result }, null, 2));

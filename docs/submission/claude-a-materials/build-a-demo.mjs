// 生成 A 方可编辑演示组件（5 页），并用 @oai/artifact-tool 渲染逐页 PNG 与 finalizer 检查。
// 用法（在已配置 bundled Node 的环境）：
//   RUNTIME_NODE_MODULES=<bundled node_modules> node build-a-demo.mjs
// 产物：
//   docs/submission/claude-a-materials/A-DEMO-COMPONENT.pptx  （最终 PPTX）
//   work/claude-a-materials/png/slide-*.png                    （逐页渲染，供 A 视觉复核）
//   work/claude-a-materials/deck-montage.webp                  （整组概览）
//   work/claude-a-materials/A-DEMO-COMPONENT.validation.json   （finalizer 报告）
// 只写新材料目录，不改业务代码/契约/配置。

import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import path from "node:path";
import fs from "node:fs/promises";

const RUNTIME_NODE_MODULES =
  "C:/Users/LXX CC/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules";
const RUNTIME_PYTHON =
  "C:/Users/LXX CC/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe";
const SKILL_DIR =
  "C:/Users/LXX CC/.codex/plugins/cache/openai-primary-runtime/presentations/26.1022.11436/skills/presentations";

const requireFromRuntime = createRequire(
  path.join(RUNTIME_NODE_MODULES, "__runtime__.cjs"),
);
const artifactToolEntry = requireFromRuntime.resolve("@oai/artifact-tool");
const { Presentation, PresentationFile } = await import(
  pathToFileURL(artifactToolEntry).href
);
const { finalizePresentation, makeNativeBulletParagraphs } = await import(
  pathToFileURL(path.join(SKILL_DIR, "container_tools", "artifact_tool_utils.mjs")).href
);

// 仓库根：docs/submission/claude-a-materials -> 上三级
const REPO_ROOT = path.resolve(import.meta.dirname, "..", "..", "..");
const OUT_DIR = path.join(REPO_ROOT, "docs", "submission", "claude-a-materials");
const WORK_DIR = path.join(REPO_ROOT, "work", "claude-a-materials");
const PNG_DIR = path.join(WORK_DIR, "png");
const STAGING_DIR = path.join(WORK_DIR, ".codex-finalizer");
const ARCH_IMAGE = path.join(
  REPO_ROOT,
  "docs",
  "submission",
  "design",
  "tikz_architecture.png",
);
const FINAL_PPTX = path.join(OUT_DIR, "A-DEMO-COMPONENT.pptx");

const FONT = "Microsoft YaHei";
const C = {
  ink: "#0f172a",
  body: "#334155",
  sub: "#64748b",
  ok: "#047857",
  warn: "#b45309",
  line: "#e2e8f0",
  white: "#ffffff",
};

await fs.mkdir(PNG_DIR, { recursive: true });
await fs.mkdir(STAGING_DIR, { recursive: true });
await fs.mkdir(OUT_DIR, { recursive: true });

function textBox(slide, { left, top, width, height, fontSize, color, bold, paragraphs, lineSpacing }) {
  const s = slide.shapes.add({
    geometry: "textbox",
    position: { left, top, width, height },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  s.text = paragraphs;
  s.text.style = {
    typeface: FONT,
    fontSize,
    color,
    bold: !!bold,
    lineSpacing,
  };
  return s;
}

const bullets = (items) =>
  makeNativeBulletParagraphs(items, {
    marginLeftPoints: 18,
    hangingPoints: 9,
    spaceAfterPoints: 6,
  });

function title(slide, text) {
  textBox(slide, {
    left: 72, top: 44, width: 1136, height: 60,
    fontSize: 40, color: C.ink, bold: true, paragraphs: text,
  });
}

function footnote(slide, text) {
  textBox(slide, {
    left: 72, top: 656, width: 1136, height: 40,
    fontSize: 18, color: C.sub, paragraphs: text,
  });
}

function notes(slide, lines) {
  slide.speakerNotes.text = lines;
  slide.speakerNotes.setVisible(true);
}

const presentation = Presentation.create({
  slideSize: { width: 1280, height: 720 },
});

// ---------- 第 1 页：冷库业务与角色 ----------
{
  const s = presentation.slides.add();
  s.background.fill = C.white;
  title(s, "冷库业务与角色范围");

  textBox(s, {
    left: 72, top: 128, width: 560, height: 40,
    fontSize: 28, color: C.ink, bold: true, paragraphs: "业务范围",
  });
  textBox(s, {
    left: 72, top: 176, width: 560, height: 220,
    fontSize: 24, color: C.body, paragraphs: bullets([
      "两座苹果冷库，三批同品种苹果，十四个逻辑仿真设备",
      "七个测区，每区一个温度采集身份、一个制冷执行身份",
      "数据来自软件模拟，不是实物果品实验；阈值异常不代表真实腐败概率",
    ]),
  });

  textBox(s, {
    left: 680, top: 128, width: 530, height: 40,
    fontSize: 28, color: C.ink, bold: true, paragraphs: "数据与处置链",
  });
  textBox(s, {
    left: 680, top: 176, width: 530, height: 300,
    fontSize: 24, color: C.body,
    paragraphs: [
      { spaceAfter: 600, runs: [{ run: "采集：", style: { bold: true } }, "设备温度消息持续上报"] },
      { spaceAfter: 600, runs: [{ run: "观测：", style: { bold: true } }, "原始、清洗、历史与同筛选 CSV"] },
      { spaceAfter: 600, runs: [{ run: "判定：", style: { bold: true } }, "B 版本化规则判断异常"] },
      { spaceAfter: 600, runs: [{ run: "处置：", style: { bold: true } }, "下发制冷命令、产生告警并保留追溯"] },
    ],
  });

  textBox(s, {
    left: 72, top: 500, width: 1136, height: 40,
    fontSize: 28, color: C.ink, bold: true, paragraphs: "角色分工",
  });
  textBox(s, {
    left: 72, top: 548, width: 1136, height: 96,
    fontSize: 24, color: C.body,
    paragraphs: [
      { spaceAfter: 400, runs: [{ run: "A：", style: { bold: true } }, "设备注册、在线状态、MQTT 接纳、控制回执与数据链接线"] },
      { spaceAfter: 400, runs: [{ run: "B：", style: { bold: true } }, "冷库批次、规则、告警处置、两种通知与追溯"] },
      { spaceAfter: 0, runs: [{ run: "C：", style: { bold: true } }, "遥测清洗、质量、历史、快照 CSV 与风险评价"] },
    ],
  });

  footnote(s, "来源 docs/submission/A-SYSTEM-DESIGN.md；设备运行层 8cd5f2d（A35）");
  notes(s, [
    "来源相对路径：docs/submission/A-SYSTEM-DESIGN.md（§1 需求与边界）",
    "设备运行来源：docs/modules/A/35-fourteen-device-runtime.md，核心提交 8cd5f2dac57df94699b2e06e1c398320b5393eb5",
    "对应真实证据：14 个独立身份真实心跳、七路遥测、七执行器各执行 1 次、重放原 ACK 完全一致、七区后续采样温度下降（fleet.json）",
    "未测/不代表：该运行不含 A 注册持久 worker、B 三批规则、C 存储历史 CSV、两通知、第二电脑；不是完整 E1。",
  ]);
}

// ---------- 第 2 页：共享系统架构 ----------
{
  const s = presentation.slides.add();
  s.background.fill = C.white;
  title(s, "共享系统架构与模块归属");

  textBox(s, {
    left: 72, top: 128, width: 430, height: 40,
    fontSize: 28, color: C.ink, bold: true, paragraphs: "分层与归属",
  });
  textBox(s, {
    left: 72, top: 176, width: 430, height: 460,
    fontSize: 22, color: C.body,
    paragraphs: bullets([
      "展示层：React/TypeScript/Vite，公共认证与导航",
      "HTTP 层：FastAPI，总入口一次挂载 API_V1_STR",
      "A 入口：注册、心跳在线、MQTT 接纳、控制回执、A→B 投递",
      "B 规则告警：批次、版本化规则、异常事件、通知 attempt",
      "C 数据质量：raw/质量、历史、固定快照 CSV、评价与 outbox",
      "存储：PostgreSQL 业务库 + Mosquitto 消息路由",
    ]),
  });

  try {
    const bytes = await fs.readFile(ARCH_IMAGE);
    const ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    s.images.add({
      blob: ab,
      contentType: "image/png",
      alt: "鲜链智控当前系统架构图",
      fit: "contain",
      position: { left: 540, top: 140, width: 668, height: 480 },
    });
  } catch (e) {
    textBox(s, {
      left: 540, top: 140, width: 668, height: 120,
      fontSize: 20, color: C.warn,
      paragraphs: "架构图未读取成功：" + String(e).slice(0, 80),
    });
  }

  footnote(s, "图 docs/submission/design/tikz_architecture.png（TikZ 源 tikz_architecture.tex）");
  notes(s, [
    "来源相对路径：docs/submission/A-SYSTEM-DESIGN.md（§2 分层架构）",
    "架构图来源：docs/submission/design/README.md；业务源码固定 71e5a8820ee966db1b250afaf0a72143a4bdc62b（PR59）",
    "图已实际编译、渲染并逐张人工视觉检查；图中通知实收为虚线，表示待验收",
    "未测：图中组件存在不代表所有后台调度已启用；B 独立持久派发、C 自动恢复、SMTP＋飞书两端实收仍未签收。",
  ]);
}

// ---------- 第 3 页：控制与数据链 ----------
{
  const s = presentation.slides.add();
  s.background.fill = C.white;
  title(s, "控制与数据链的分段证据");

  textBox(s, {
    left: 72, top: 128, width: 360, height: 40,
    fontSize: 28, color: C.ink, bold: true, paragraphs: "数据链环节",
  });
  textBox(s, {
    left: 72, top: 176, width: 360, height: 400,
    fontSize: 24, color: C.body,
    paragraphs: [
      "1  注册绑定设备",
      "2  在线状态与心跳",
      "3  下发制冷命令",
      "4  设备执行并返回 ACK",
      "5  后续采样温度变化",
      "6  raw/清洗/历史/同筛选 CSV",
    ].map((t) => ({ spaceAfter: 800, runs: [t] })),
  });

  textBox(s, {
    left: 470, top: 128, width: 738, height: 40,
    fontSize: 28, color: C.ok, bold: true, paragraphs: "已分段证明",
  });
  textBox(s, {
    left: 470, top: 176, width: 738, height: 240,
    fontSize: 22, color: C.body,
    paragraphs: bullets([
      "注册/在线/控制 ACK 后降温、同快照 CSV 完整 ID 对拍（A46 62bb2ef）",
      "14 身份真实心跳、执行 1 次、重放 ACK 一致（A35 8cd5f2d）",
      "当前 C 只读端口兼容：历史/CSV 禁缓存、冻结前沿只读核对（A55 ecf19d0）",
    ]),
  });

  textBox(s, {
    left: 470, top: 424, width: 738, height: 40,
    fontSize: 28, color: C.warn, bold: true, paragraphs: "未完成，不能写成通过",
  });
  textBox(s, {
    left: 470, top: 472, width: 738, height: 160,
    fontSize: 22, color: C.body,
    paragraphs: bullets([
      "正式恢复端口与 F03 更正接线",
      "同一冻结版本完整 E1（端到端全链）",
      "SMTP 与飞书手机端两渠道终端实收",
    ]),
  });

  footnote(s, "证据见 docs/submission/A-TEST-REPORT-CANDIDATE.md 与 docs/modules/A/46、55");
  notes(s, [
    "来源相对路径：docs/submission/A-TEST-REPORT-CANDIDATE.md",
    "A46 来源：docs/modules/A/46-gated-fleet-storage-control.md，干净 62bb2ef：14 在线、7 采集流、执行 ACK 后降温、停发离线同状态恢复、同快照 CSV 完整 ID 对拍（52/39 行）",
    "A55 来源：docs/modules/A/55-current-c-read-ports.md，精确 ecf19d092075fa825e565d7f5f47c39519432ead，63 项全通过",
    "A35 来源：docs/modules/A/35-fourteen-device-runtime.md，核心提交 8cd5f2dac57df94699b2e06e1c398320b5393eb5",
    "未测/不代表：A55 complete_e1=false、notifications_sent=0；A46 WH1 多批评价仍 hold，不是多批告警、不是 C 自动恢复、不是两通知实收。",
  ]);
}

// ---------- 第 4 页：可靠性与真实验证 ----------
{
  const s = presentation.slides.add();
  s.background.fill = C.white;
  title(s, "可靠性与真实验证边界");

  textBox(s, {
    left: 72, top: 120, width: 1136, height: 44,
    fontSize: 24, color: C.body,
    paragraphs: "候选事务与重启验证，逐项区分“证明了什么、没证明什么”。",
  });

  const values = [
    ["版本 / 运行", "实际证明", "不代表什么"],
    ["1b7a9d9（A54）", "1341 项零失败/错误/跳过、92%；唯一迁移链空库升级；真实 MQTT→CLI→C PG→TCP B→合成渠道", "不是 4db 最新全套重跑，不是终端实收"],
    ["ecf19d0（A55）", "63 项全通过；当前 C 只读端口兼容", "不是 SMTP/飞书实收，complete_e1=false"],
    ["62bb2ef（A46）", "14 在线、7 采集流、执行 ACK 后降温、离线同状态恢复、同快照 CSV 对拍", "不是多批告警，不是完整 E1"],
    ["合成派发 0/2/3/3", "持久 attempt 重启只补缺失、不重复发送", "合成 SMTP/APP 不是真服务，不是双渠道实收"],
  ];

  const table = s.tables.add({
    rows: 5,
    columns: 3,
    left: 72,
    top: 180,
    width: 1136,
    height: 430,
    values,
  });
  table.borders.assign({ style: "solid", fill: C.line, width: 1 });
  table.cells
    .block({ row: 0, column: 0, rowCount: 5, columnCount: 3 })
    .assign({ textStyle: { typeface: FONT, fontSize: 18, color: C.ink } });
  table.cells
    .block({ row: 0, column: 0, rowCount: 1, columnCount: 3 })
    .assign({ fill: C.ink, textStyle: { typeface: FONT, fontSize: 18, bold: true, color: C.white } });
  table.columns.get(0).width = 240;
  table.columns.get(1).width = 520;
  table.columns.get(2).width = 376;

  footnote(s, "证据 A54/A55/A46；见 docs/modules/A/54、55、46");
  notes(s, [
    "来源相对路径：docs/modules/A/54-notification-runner-composition.md、55-current-c-read-ports.md、46-gated-fleet-storage-control.md",
    "A54 精确提交 1b7a9d91e6a8a426eb4995e7f9114bff1a399ba7；A55 精确提交 ecf19d092075fa825e565d7f5f47c39519432ead；A46 干净 62bb2ef",
    "可靠机制：原异常行锁/锁后重读/事务失败回滚/HTTP 503+Retry-After；MQTT 持久接纳；固定快照；合成派发 attempt 0/2/3/3",
    "未测/不代表：合成 SMTP/APP 不是真服务；两渠道终端实收、同一版本完整 E1、第二电脑最终冻结版复现均未签收。",
  ]);
}

// ---------- 第 5 页：验收与交付 ----------
{
  const s = presentation.slides.add();
  s.background.fill = C.white;
  title(s, "验收与交付状态");

  textBox(s, {
    left: 72, top: 128, width: 560, height: 40,
    fontSize: 28, color: C.ok, bold: true, paragraphs: "已通过的候选能力",
  });
  textBox(s, {
    left: 72, top: 176, width: 560, height: 300,
    fontSize: 22, color: C.body,
    paragraphs: bullets([
      "设备注册/在线、控制命令与执行 ACK、后续温度变化",
      "raw/清洗/历史与同筛选 CSV 完整对拍",
      "唯一迁移链空库升级、持久接纳、固定快照、合成派发重启",
      "后端回归 1341 项、当前 C 增量 63 项通过",
    ]),
  });

  textBox(s, {
    left: 680, top: 128, width: 530, height: 40,
    fontSize: 28, color: C.warn, bold: true, paragraphs: "待填，不勾选",
  });
  textBox(s, {
    left: 680, top: 176, width: 530, height: 300,
    fontSize: 22, color: C.body,
    paragraphs: bullets([
      "正式恢复端口与 F03 更正接线",
      "SMTP 与飞书手机端真实授权实收",
      "同一冻结版本完整 E1 与第二电脑复现",
      "M01—M05 汇编与实际投递",
    ]),
  });

  textBox(s, {
    left: 72, top: 520, width: 1136, height: 92,
    fontSize: 24, color: C.ink, bold: true,
    paragraphs: "本材料只标候选与分段证据；最终勾选须等真实结果与同版复核，不伪造截图、收件或通过勾号。",
  });

  footnote(s, "交付门禁 docs/submission/README.md；A54/A55 为候选，非已发布 main");
  notes(s, [
    "来源相对路径：docs/submission/README.md（发布阻断检查 S0/E1/F/第二机/M01—M05）",
    "候选基线：本分支业务源 4dbf136bef902df3018ec322a0eeb18b9909a157，是 PR76 main-facing 候选，不是已发布 main",
    "已发布 main 在下发时 e20f553bb85a67ac0ec74579a9fcc238b5d427a8",
    "未测/不代表：完整 E1、两渠道终端实收、最终冻结版第二机、M01—M05 成品与实际投递均未签收。",
  ]);
}

// ---------- 逐页渲染 PNG ----------
for (const [index, slide] of presentation.slides.items.entries()) {
  const stem = `slide-${String(index + 1).padStart(2, "0")}`;
  const png = await presentation.export({ slide, format: "png", scale: 1 });
  await fs.writeFile(
    path.join(PNG_DIR, `${stem}.png`),
    new Uint8Array(await png.arrayBuffer()),
  );
}
try {
  const montage = await presentation.export({ format: "webp", montage: true });
  await fs.writeFile(
    path.join(WORK_DIR, "deck-montage.webp"),
    new Uint8Array(await montage.arrayBuffer()),
  );
} catch (e) {
  console.error("montage failed:", String(e));
}

// ---------- finalizer 检查并写最终 PPTX ----------
async function writeBlob(filePath, blob) {
  await fs.writeFile(filePath, new Uint8Array(await blob.arrayBuffer()));
}

const requirements = {
  explicitTotalSlideCount: 5,
  requiredNativeTableOwnerSlides: [4],
  requiredNativeChartOwnerSlides: [],
};
const fontPolicy = { basis: "design", families: [FONT] };
const expectedSlideSizeEmu = "12192000,6858000";

let finalizerReport = null;
let finalizerError = null;
let usedFinalizer = false;

try {
  const candidatePath = path.join(STAGING_DIR, "candidate.pptx");
  await (await PresentationFile.exportPptx(presentation)).save(candidatePath);

  const receiptPath = path.join(STAGING_DIR, "A-DEMO-COMPONENT.validation.json");
  const result = await finalizePresentation({
    ...requirements,
    workspaceDir: REPO_ROOT,
    candidatePath,
    finalPath: FINAL_PPTX,
    pythonExecutable: RUNTIME_PYTHON,
    integrityValidatorPath: path.join(SKILL_DIR, "container_tools", "inspect_presentation_package_integrity.py"),
    layoutValidatorPath: path.join(SKILL_DIR, "container_tools", "inspect_presentation_layout_geometry.py"),
    layoutArgs: [
      "--expected-slide-size-emu", expectedSlideSizeEmu,
      "--validate-bullet-geometry",
      "--validate-heading-fit",
      "--require-native-table-slide", "4",
    ],
    requiredNativeTableOwnerSlides: [4],
    fontPolicy,
    verifyArtifactToolImport: true,
    receiptPath,
  });
  finalizerReport = result;
  usedFinalizer = true;
  const reportText = JSON.stringify(result, null, 2);
  await fs.writeFile(
    path.join(WORK_DIR, "A-DEMO-COMPONENT.validation.json"),
    reportText,
  );
  console.log("FINALIZER_OK");
  console.log(reportText.slice(0, 2000));
} catch (e) {
  finalizerError = String(e);
  console.error("FINALIZER_FAILED:", finalizerError);
  // 回退：直接用导出 PPTX，保留错误供报告
  await (await PresentationFile.exportPptx(presentation)).save(FINAL_PPTX);
}

const summary = {
  slides: presentation.slides.items.length,
  finalPptx: FINAL_PPTX,
  finalPptxBytes: (await fs.stat(FINAL_PPTX).catch(() => ({ size: -1 }))).size,
  pngDir: PNG_DIR,
  usedFinalizer,
  finalizerError,
  finalizerReport,
};
await fs.writeFile(
  path.join(WORK_DIR, "build-summary.json"),
  JSON.stringify(summary, null, 2),
);
console.log("BUILD_DONE", JSON.stringify({
  slides: summary.slides,
  finalPptxBytes: summary.finalPptxBytes,
  usedFinalizer,
  finalizerError,
}));

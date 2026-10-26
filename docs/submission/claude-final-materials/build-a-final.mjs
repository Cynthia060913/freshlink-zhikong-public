// 生成 A 方可编辑演示组件（6 页），并用 @oai/artifact-tool 渲染逐页 PNG 与 finalizer 检查。
// 复用旧 PR77 五页原生可编辑组件与生成方法，更新至本任务固定来源 main0e。
// 用法（在已配置 bundled Node 的环境）：
//   RUNTIME_NODE_MODULES=<bundled node_modules> \
//   RUNTIME_PYTHON=<bundled python> \
//   SKILL_DIR=<presentations skill dir> \
//   node build-a-final.mjs
// 产物：
//   docs/submission/claude-final-materials/A-FINAL-COMPONENT.pptx  （最终 PPTX）
//   work/claude-final-materials/png/slide-*.png                    （逐页渲染，供 A 视觉复核）
//   work/claude-final-materials/A-FINAL-COMPONENT.validation.json （finalizer 报告）
// 只写新材料目录，不改业务代码/契约/配置/依赖锁。
//
// 图片来源（真实截图/已提交图件）：
//   docs/submission/design/tikz_architecture.png   （系统架构图，已编译渲染）
//   docs/submission/design/tikz_er_a.png           （A 设备控制 ER，已编译渲染）
//   work/claude-final-materials/assets/admin-real-ack.png  （A 真实浏览器控制 ACK 截图）
// 后两项截图来自 A 私有运行证据白名单，脚本按绝对/本地 assets 路径只读，缺失时回退为
// 文字占位说明，不伪造效果。渲染 PNG 仅放本地 work/，不提交。

import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import path from "node:path";
import fs from "node:fs/promises";

const { RUNTIME_NODE_MODULES, RUNTIME_PYTHON, SKILL_DIR } = process.env;
if (!RUNTIME_NODE_MODULES || !RUNTIME_PYTHON || !SKILL_DIR) {
  throw new Error("Set explicit bundled RUNTIME_NODE_MODULES, RUNTIME_PYTHON and SKILL_DIR paths; no dependency installation is performed.");
}

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

// 仓库根：docs/submission/claude-final-materials -> 上三级
const REPO_ROOT = path.resolve(import.meta.dirname, "..", "..", "..");
const OUT_DIR = path.join(REPO_ROOT, "docs", "submission", "claude-final-materials");
const WORK_DIR = path.join(REPO_ROOT, "work", "claude-final-materials");
const PNG_DIR = path.join(WORK_DIR, "png");
const STAGING_DIR = path.join(WORK_DIR, ".codex-finalizer");
const ASSET_DIR = path.join(WORK_DIR, "assets");

const ARCH_IMAGE = path.join(REPO_ROOT, "docs", "submission", "design", "tikz_architecture.png");
const ER_A_IMAGE = path.join(REPO_ROOT, "docs", "submission", "design", "tikz_er_a.png");
const ADMIN_ACK_IMAGE = path.join(ASSET_DIR, "admin-real-ack.png");

const outputName = process.env.A_DEMO_OUTPUT_NAME || "A-FINAL-COMPONENT.pptx";
if (path.basename(outputName) !== outputName || !outputName.endsWith(".pptx")) {
  throw new Error("A_DEMO_OUTPUT_NAME must be a PPTX filename, not a path.");
}
const FINAL_PPTX = path.join(OUT_DIR, outputName);

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
    fontSize: 38, color: C.ink, bold: true, paragraphs: text,
  });
}

function footnote(slide, text) {
  textBox(slide, {
    left: 72, top: 660, width: 1136, height: 40,
    fontSize: 16, color: C.sub, paragraphs: text,
  });
}

function notes(slide, lines) {
  slide.speakerNotes.text = lines;
  slide.speakerNotes.setVisible(true);
}

async function addImage(slide, imagePath, alt, position, fallbackText) {
  try {
    const bytes = await fs.readFile(imagePath);
    const ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    slide.images.add({
      blob: ab,
      contentType: "image/png",
      alt,
      fit: "contain",
      position,
    });
    return true;
  } catch (e) {
    textBox(slide, {
      ...position,
      fontSize: 18, color: C.warn,
      paragraphs: fallbackText + String(e).slice(0, 80),
    });
    return false;
  }
}

const presentation = Presentation.create({
  slideSize: { width: 1280, height: 720 },
});

// ---------- 第 1 页：真实范围与角色 ----------
{
  const s = presentation.slides.add();
  s.background.fill = C.white;
  title(s, "冷库业务与角色范围");

  textBox(s, {
    left: 72, top: 128, width: 560, height: 40,
    fontSize: 26, color: C.ink, bold: true, paragraphs: "业务范围",
  });
  textBox(s, {
    left: 72, top: 176, width: 560, height: 200,
    fontSize: 22, color: C.body, paragraphs: bullets([
      "两座苹果冷库、三批同品种苹果、七个测区",
      "每区一个温度采集身份、一个制冷执行身份，共十四个逻辑设备",
      "数据来自软件模拟，不是实物果品实验；阈值异常不代表真实腐败概率",
    ]),
  });

  textBox(s, {
    left: 680, top: 128, width: 530, height: 40,
    fontSize: 26, color: C.ink, bold: true, paragraphs: "数据与处置链",
  });
  textBox(s, {
    left: 680, top: 176, width: 530, height: 280,
    fontSize: 22, color: C.body,
    paragraphs: [
      { spaceAfter: 600, runs: [{ run: "采集：", style: { bold: true } }, "设备温度消息持续上报"] },
      { spaceAfter: 600, runs: [{ run: "观测：", style: { bold: true } }, "原始、清洗、历史与同筛选 CSV"] },
      { spaceAfter: 600, runs: [{ run: "判定：", style: { bold: true } }, "B 版本化规则判断异常"] },
      { spaceAfter: 600, runs: [{ run: "处置：", style: { bold: true } }, "下发制冷命令、产生告警并保留追溯"] },
    ],
  });

  textBox(s, {
    left: 72, top: 492, width: 1136, height: 40,
    fontSize: 26, color: C.ink, bold: true, paragraphs: "角色分工",
  });
  textBox(s, {
    left: 72, top: 540, width: 1136, height: 110,
    fontSize: 21, color: C.body,
    paragraphs: [
      { spaceAfter: 300, runs: [{ run: "A：", style: { bold: true } }, "设备注册、在线状态、MQTT 接纳、控制回执与数据链接线"] },
      { spaceAfter: 300, runs: [{ run: "B：", style: { bold: true } }, "冷库批次、规则、告警处置、两种通知与追溯"] },
      { spaceAfter: 0, runs: [{ run: "C：", style: { bold: true } }, "遥测清洗、质量、历史、快照 CSV 与风险评价"] },
    ],
  });

  footnote(s, "来源 docs/submission/A-SYSTEM-DESIGN.md §1；软件来源 main0e 0e508d1");
  notes(s, [
    "来源相对路径：docs/submission/A-SYSTEM-DESIGN.md（§1 需求与边界）",
    "本组件固定软件来源：main0e 0e508d147517f81dfe2b9245c45c0c0e30076592（PR82/88 已合并）",
    "范围：两库三批同品种苹果、七测区十四逻辑设备；数据为软件模拟，非实物果品实验",
    "未测/不代表：两通知终端实收、完整 E1、第二机、M01—M05 与实际投递均未签收。",
  ]);
}

// ---------- 第 2 页：共享系统架构与模块归属 ----------
{
  const s = presentation.slides.add();
  s.background.fill = C.white;
  title(s, "共享系统架构与模块归属");

  textBox(s, {
    left: 72, top: 128, width: 460, height: 40,
    fontSize: 26, color: C.ink, bold: true, paragraphs: "分层与归属",
  });
  textBox(s, {
    left: 72, top: 176, width: 460, height: 460,
    fontSize: 20, color: C.body,
    paragraphs: bullets([
      "展示层：React/TypeScript/Vite，公共认证与导航",
      "HTTP 层：FastAPI，总入口一次挂载 API_V1_STR",
      "A 入口：注册、心跳在线、MQTT 接纳、控制回执、A→B 投递",
      "B 规则告警：批次、版本化规则、异常事件、通知 attempt",
      "C 数据质量：raw/质量、历史、固定快照 CSV、评价与 outbox",
      "存储：PostgreSQL 业务库 + Mosquitto 消息路由",
    ]),
  });

  await addImage(s, ARCH_IMAGE, "鲜链智控当前系统架构图",
    { left: 560, top: 132, width: 648, height: 500 },
    "架构图未读取成功：");

  footnote(s, "图 docs/submission/design/tikz_architecture.png（TikZ 源 tikz_architecture.tex）");
  notes(s, [
    "来源相对路径：docs/submission/A-SYSTEM-DESIGN.md（§2 分层架构）",
    "架构图来源：docs/submission/design/README.md；图已实际编译、渲染并逐张人工视觉检查",
    "图中组件存在不表示所有后台调度已启用；通知实收为虚线，表示待验收",
    "未测：B 独立持久派发、C 自动恢复、SMTP＋飞书两端实收仍未签收。",
  ]);
}

// ---------- 第 3 页：采集、控制与恢复的实际证据 ----------
{
  const s = presentation.slides.add();
  s.background.fill = C.white;
  title(s, "采集、控制与恢复的实际证据");

  textBox(s, {
    left: 72, top: 128, width: 560, height: 40,
    fontSize: 26, color: C.ok, bold: true, paragraphs: "已分段证明（main0e）",
  });
  textBox(s, {
    left: 72, top: 176, width: 560, height: 460,
    fontSize: 20, color: C.body,
    paragraphs: bullets([
      "采集：真实 MQTT 温度消息 → A 校验身份/绑定并持久接纳 → C 同事务保存",
      "观测：raw/清洗/历史与同筛选 CSV 完整 ID 对拍（浏览器 23 行一致）",
      "控制：下发 set_cooling → MQTT 执行 → 设备 ACK → 后续采样降温",
      "恢复：后台受保护 TCP 恢复发送，回读原异常 resolved（见第 4 页）",
      "控制实测 10.003187 → 9.851915 °C，是软件仿真，非实物制冷",
    ]),
  });

  await addImage(s, ADMIN_ACK_IMAGE, "A 真实浏览器控制命令与设备执行 ACK 截图",
    { left: 660, top: 128, width: 548, height: 470 },
    "控制 ACK 截图未读取成功：");

  footnote(s, "证据 control-browser-20261027001102-63760/browser.json 与 server.json；main0e");
  notes(s, [
    "来源相对路径：docs/submission/A-TEST-REPORT-CANDIDATE.md、A64-INTEGRATION-CHECKPOINT.md",
    "控制 ACK 截图：admin-real-ack.png，command_id 3aad6c63，set_cooling，execution_count=1，ACK 后温度下降",
    "遥测截图（供 A 选用，本组件未嵌）：telemetry-real-history-raw-csv.png，snapshot 23 行、同筛选 CSV 23 行一致",
    "边界：高温三批为 ASGI HTTP 到 B（非 TCP）；notifications_sent=0、complete_e1=false；不是完整 E1。",
  ]);
}

// ---------- 第 4 页：受保护的恢复后台发送与同版十四设备 ----------
{
  const s = presentation.slides.add();
  s.background.fill = C.white;
  title(s, "受保护的恢复后台发送与同版十四设备");

  textBox(s, {
    left: 72, top: 120, width: 1136, height: 40,
    fontSize: 21, color: C.body,
    paragraphs: "main0e 受保护恢复：提交成功且连接释放后才 CAS 领取、fenced 回执，重启不重发。",
  });

  const values = [
    ["运行", "实际结果", "边界"],
    ["十四设备同版", "两库三批、14 在线、7 采集流、7 执行器 ACK 后降温、14 离线恢复追溯、重启不重发", "注册 ID 是显式 ACL 夹具，非注册 API 实测"],
    ["多批高温候选", "3 批经鉴权 ASGI HTTP 到 B，来源回读批次/规则版本匹配", "ASGI 不是 TCP；显式规则切换，非物理果品升温"],
    ["受保护恢复后台", "独立 TCP 进程 CAS 领取并回读原异常 resolved；重启一次不重复投递，attempt=1", "notifications_sent=0，非终端实收"],
    ["迟到更正", "独立更正进程 → B 双 ID 追溯、控制 ACK 及后续数据", "仍有 held 组合未证明自动收敛"],
  ];

  const table = s.tables.add({
    rows: 5,
    columns: 3,
    left: 72,
    top: 178,
    width: 1136,
    height: 420,
    values,
  });
  table.borders.assign({ style: "solid", fill: C.line, width: 1 });
  table.cells
    .block({ row: 0, column: 0, rowCount: 5, columnCount: 3 })
    .assign({ textStyle: { typeface: FONT, fontSize: 17, color: C.ink } });
  table.cells
    .block({ row: 0, column: 0, rowCount: 1, columnCount: 3 })
    .assign({ fill: C.ink, textStyle: { typeface: FONT, fontSize: 17, bold: true, color: C.white } });
  table.columns.get(0).width = 220;
  table.columns.get(1).width = 560;
  table.columns.get(2).width = 356;

  footnote(s, "证据 docs/modules/A/65-guarded-recovery-delivery.md；A64-INTEGRATION-CHECKPOINT.md；gated-fleet.json");
  notes(s, [
    "来源相对路径：docs/modules/A/65-guarded-recovery-delivery.md（受保护恢复后台发送候选）",
    "十四设备证据：work/.../gated-fleet-7cc435cc846e4b25935d311fa624ca8d/gated-fleet.json（source=0e508d1）",
    "受保护恢复证据：work/.../gated-a9aed80a7c84448d89612ba511419db9/runtime-composition.json（source_revision=1c85ae2）",
    "未测/不代表：C 自动恢复、两终端实收、第二机、完整 E1；notifications_sent=0、complete_e1=false。",
  ]);
}

// ---------- 第 5 页：同版应用镜像与 33 表 ER ----------
{
  const s = presentation.slides.add();
  s.background.fill = C.white;
  title(s, "同版应用镜像与 33 表 ER");

  textBox(s, {
    left: 72, top: 128, width: 520, height: 40,
    fontSize: 26, color: C.ink, bold: true, paragraphs: "应用镜像与数据库",
  });
  textBox(s, {
    left: 72, top: 176, width: 520, height: 300,
    fontSize: 20, color: C.body,
    paragraphs: bullets([
      "应用镜像归档 540475904 字节，sha256 f7a5e3d5…bc0b806",
      "默认 CMD 真实 HTTP：健康、登录、注册、重复拒绝、详情通过",
      "33 表只读反射无 schema 变化；19 核心实体、17 条真实外键边",
      "唯一迁移链 A03→B01→A04→A05→C01 空库升级/check 通过",
      "导入/第二机仍未验证，不等于可运行交付已签收",
    ]),
  });

  await addImage(s, ER_A_IMAGE, "A 设备控制核心 ER 图",
    { left: 620, top: 132, width: 588, height: 330 },
    "ER 图未读取成功：");

  textBox(s, {
    left: 620, top: 476, width: 588, height: 130,
    fontSize: 18, color: C.sub,
    paragraphs: [
      { spaceAfter: 300, runs: [{ run: "图：", style: { bold: true } }, "设备、心跳、在线离线事件、控制命令/尝试/执行回执"] },
      { spaceAfter: 0, runs: [{ run: "来源：", style: { bold: true } }, "docs/submission/design/tikz_er_a.png（只展示真实外键）"] },
    ],
  });

  footnote(s, "证据 image-archive.json、application-http.json、design-schema-comparison.json；main0e");
  notes(s, [
    "来源相对路径：docs/submission/design/README.md（A47 实际架构、ER 与数据字典）",
    "镜像归档：image-archive-20261026231221-351a4765a356/image-archive.json；540MB tar 不读入 Git 或材料小包",
    "33 表反射比较：notify-subset-.../design-schema-comparison.json，changed_table_definitions=[]",
    "未测/不代表：镜像导入另一台电脑、运行依赖可用性、最终冻结版、完整 E1。",
  ]);
}

// ---------- 第 6 页：诚实剩余验收 ----------
{
  const s = presentation.slides.add();
  s.background.fill = C.white;
  title(s, "诚实剩余验收");

  textBox(s, {
    left: 72, top: 128, width: 560, height: 40,
    fontSize: 26, color: C.ok, bold: true, paragraphs: "已通过的候选能力",
  });
  textBox(s, {
    left: 72, top: 176, width: 560, height: 300,
    fontSize: 20, color: C.body,
    paragraphs: bullets([
      "设备注册/在线、控制命令与执行 ACK、后续温度下降",
      "raw/清洗/历史与同筛选 CSV 完整对拍",
      "唯一迁移链空库升级、33 表反射、受保护恢复后台 TCP",
      "同版十四设备分段、后端 1446 用例 / 0 跳过 / 91%",
    ]),
  });

  textBox(s, {
    left: 680, top: 128, width: 530, height: 40,
    fontSize: 26, color: C.warn, bold: true, paragraphs: "待填，不勾选",
  });
  textBox(s, {
    left: 680, top: 176, width: 530, height: 300,
    fontSize: 20, color: C.body,
    paragraphs: bullets([
      "SMTP 与飞书手机端两渠道授权实收",
      "同一冻结版本完整 E1 与最终第二机复现",
      "C 自动恢复与多批高温业务收尾",
      "M01—M05 汇编与实际投递",
    ]),
  });

  textBox(s, {
    left: 72, top: 520, width: 1136, height: 92,
    fontSize: 21, color: C.ink, bold: true,
    paragraphs: "本材料只标候选与分段证据；最终勾选须等真实结果与同版复核，不伪造截图、收件或通过勾号。",
  });

  footnote(s, "交付门禁 docs/submission/README.md；main0e 是软件来源，不代表完整 E1 已验收");
  notes(s, [
    "来源相对路径：docs/submission/README.md（发布阻断检查 S0/E1/F/第二机/M01—M05）",
    "后端回归：本地 1446 passed / 0 skipped / 91%（终端口径）；适用云端 1444 passed / 2 skipped，分别列不加总",
    "JUnit XML 汇总 tests=1755 含 subTests，不当新增独立用例；报告 work/a01-tests-20261026222407-79444/pytest.xml",
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
const requirements = {
  explicitTotalSlideCount: 6,
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

  const receiptPath = path.join(STAGING_DIR, "A-FINAL-COMPONENT.validation.json");
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
    path.join(WORK_DIR, "A-FINAL-COMPONENT.validation.json"),
    reportText,
  );
  console.log("FINALIZER_OK");
  console.log(reportText.slice(0, 2000));
} catch (e) {
  finalizerError = String(e);
  console.error("FINALIZER_FAILED:", finalizerError);
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

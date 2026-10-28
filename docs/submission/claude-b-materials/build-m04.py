"""Build M04 presentation draft (editable OOXML, stdlib only).

生成 M04 项目展示 PPT 草稿：原生文本框（可编辑，非图片拼接）+ 实际编译的
tikz 架构/ER PNG 图件。包骨架（theme/slideMaster/slideLayouts/presProps/
tableStyles）复用 A-DEMO-COMPONENT.pptx 已验证的部件；幻灯片与内容全部由
本脚本生成。不安装任何依赖，不改全局环境。

用法：仓库根目录
    .venv/Scripts/python.exe docs/submission/claude-b-materials/build-m04.py \
        --input <A组件pptx绝对/相对路径>
源 pptx 必须显式给出（不依赖任何私人 TEMP 路径），且其 SHA-256 必须
命中下方 KNOWN_SOURCES 白名单，否则构建以明确错误中止（缺输入/校验
不符都是非零退出，不产出半成品文件）。
产物：
    docs/submission/claude-b-materials/M04-PRESENTATION-DRAFT.pptx
来源与未验证标记写在每页正文里；本脚本不生成任何"已实收/已通过"表述。
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = (
    REPO / "docs" / "submission" / "claude-b-materials" / "M04-PRESENTATION-DRAFT.pptx"
)
DESIGN = REPO / "docs" / "submission" / "design"
MATERIALS = REPO / "docs" / "submission" / "claude-b-materials"

# Only decks with a verified provenance may be used as the package
# skeleton. SHA-256 values were computed from the files A delivered /
# that are committed at the cited revisions (C review 5654186372 item 2).
KNOWN_SOURCES: dict[str, str] = {
    # A-FINAL-COMPONENT.pptx, PR #89 head e2065927 (A's stated SHA256,
    # re-verified by B from the git blob).
    "a1a041f7da3e2fa233de4eecc0a04a3e4022349912b7a76525eb357ec7428f21": "A-FINAL-COMPONENT.pptx @ PR89 e2065927",
    # A-DEMO-COMPONENT.pptx, A 早期演示组件（M04 现稿构建来源）。
    "3044ffdfc9b9f7036babd784bbf1ff763773f2be65686c86f9e5095ed6c0158f": "A-DEMO-COMPONENT.pptx",
}

# Current architecture image (A's e7-based diagram, PR #89 head
# b4d8c28, taken into B's assets verbatim). Verified before any output
# is written (A handover #8 2026-10-28, CURRENT-EVIDENCE-e7.md).
ARCH_ASSET = MATERIALS / "assets" / "CURRENT-ARCHITECTURE-e7.png"
ARCH_ASSET_SHA256 = "81a7cbf11cd689891663211ed83991c746ffbdefe32f7befc1c0488a9e4778f8"

FONT = "Microsoft YaHei"
INK = "0F172A"
BODY = "334155"
SUB = "64748B"
OK = "047857"
WARN = "B45309"
WHITE = "FFFFFF"
BAND = "1E3A5F"

SLIDE_W = 12192000
SLIDE_H = 6858000


def emu(inches: float) -> int:
    return int(inches * 914400)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build M04 presentation draft")
    parser.add_argument(
        "--input",
        required=True,
        help="path to the A component pptx used as package skeleton "
        "(SHA-256 must match KNOWN_SOURCES)",
    )
    return parser.parse_args()


def _open_source(raw_arg: str) -> zipfile.ZipFile:
    """Open the source deck, refusing missing files and unknown hashes.

    Fails loudly (exit 2) BEFORE any output file is touched: a wrong or
    silently substituted skeleton would invalidate every reused part.
    """

    path = Path(raw_arg).expanduser()
    if not path.is_file():
        print(
            f"ERROR: input pptx not found: {path}\n"
            "pass an explicit --input path to the A component deck",
            file=sys.stderr,
        )
        raise SystemExit(2)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    label = KNOWN_SOURCES.get(digest)
    if label is None:
        print(
            "ERROR: input pptx SHA-256 is not in KNOWN_SOURCES: "
            f"{digest}\nfile: {path}\n"
            "refusing to build from an unverified deck; if A reissued the "
            "component, verify the new hash and update KNOWN_SOURCES",
            file=sys.stderr,
        )
        raise SystemExit(2)
    print(f"source verified: {label} sha256={digest}")
    return zipfile.ZipFile(path)


def run(text: str, sz: int, *, b: bool = False, color: str = BODY) -> str:
    bold = ' b="1"' if b else ""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        f'<a:r><a:rPr lang="zh-CN" sz="{sz}"{bold} dirty="0">'
        f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
        f'<a:latin typeface="{FONT}"/><a:ea typeface="{FONT}"/></a:rPr>'
        f"<a:t>{text}</a:t></a:r>"
    )


def para(runs: str, *, algn: str = "l", space_after: int = 60) -> str:
    return (
        f'<a:p><a:pPr algn="{algn}"><a:spcAft><a:spcPts val="{space_after}"/>'
        f"</a:spcAft></a:pPr>{runs}</a:p>"
    )


def textbox(sp_id: int, name: str, x: int, y: int, cx: int, cy: int, paras: str) -> str:
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{sp_id}" name="{name}"/>'
        f'<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
        f'<p:txBody><a:bodyPr wrap="square" lIns="45720" tIns="27432" rIns="45720" bIns="27432"/>'
        f"<a:lstStyle/>{paras}</p:txBody></p:sp>"
    )


def band(sp_id: int, title: str, kicker: str) -> str:
    """Title band: filled rect + white title text + small kicker."""
    rect = (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{sp_id}" name="band"/>'
        f"<p:cNvSpPr/><p:nvPr/></p:nvSpPr>"
        f'<p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{SLIDE_W}" cy="{emu(1.05)}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        f'<a:solidFill><a:srgbClr val="{BAND}"/></a:solidFill><a:ln><a:noFill/></a:ln></p:spPr>'
        f'<p:txBody><a:bodyPr anchor="ctr" lIns="457200"/><a:lstStyle/>'
        + para(
            run(title, 2400, b=True, color=WHITE)
            + run("　" + kicker, 1200, color="C7D2E4"),
            space_after=0,
        )
        + "</p:txBody></p:sp>"
    )
    return rect


def picture(sp_id: int, name: str, rid: str, x: int, y: int, cx: int, cy: int) -> str:
    return (
        f'<p:pic><p:nvPicPr><p:cNvPr id="{sp_id}" name="{name}"/>'
        f'<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
        f'<p:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
    )


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as fh:
        head = fh.read(24)
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def fit_png(path: Path, box_w: int, box_h: int) -> tuple[int, int]:
    w, h = png_size(path)
    scale = min(box_w / w, box_h / h)
    return int(w * scale), int(h * scale)


def slide_xml(shapes: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        "<p:cSld><p:spTree>"
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        "<p:grpSpPr/>" + shapes + "</p:spTree></p:cSld>"
        "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
    )


def slide_rels(layout_id: str, images: dict[str, str]) -> str:
    rels = [
        (
            '<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" '
            f'Target="/ppt/slideLayouts/slideLayout6.xml" Id="{layout_id}" />'
        )
    ]
    for rid, target in images.items():
        rels.append(
            '<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="{target}" Id="{rid}" />'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(rels)
        + "</Relationships>"
    )


LAYOUT_RID = "R036352927e214d08"  # A deck slides reference layout6 by this id


def bullets(
    items: list[tuple[str, str, str, int]],
    x: int,
    y: int,
    cx: int,
    cy: int,
    sp_id: int = 20,
) -> str:
    """items: (text, color, bold, size_pt*100)."""
    paras = "".join(
        para(run(t, s, b=(b == "1"), color=c), space_after=120) for t, c, b, s in items
    )
    return textbox(sp_id, "bullets", x, y, cx, cy, paras)


def _verified_arch_asset() -> Path:
    """Verify the tracked architecture asset before writing any output.

    The image is A's e7-based current architecture diagram (PR #89 head
    b4d8c28, copied verbatim into assets/ with its provenance recorded
    in M03 §2). A wrong or silently substituted image would put an
    unverified diagram into the deck, so a mismatch fails loudly (exit
    2) before the output file is touched.
    """

    if not ARCH_ASSET.is_file():
        print(
            f"ERROR: architecture asset not found: {ARCH_ASSET}\n"
            "expected the tracked copy of CURRENT-ARCHITECTURE-e7.png "
            "(PR #89 b4d8c28)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    digest = hashlib.sha256(ARCH_ASSET.read_bytes()).hexdigest()
    if digest != ARCH_ASSET_SHA256:
        print(
            "ERROR: architecture asset SHA-256 mismatch:\n"
            f"expected {ARCH_ASSET_SHA256}\nactual   {digest}\n"
            "refusing to build with an unverified diagram; if A reissued "
            "the image, verify the new hash and update ARCH_ASSET_SHA256",
            file=sys.stderr,
        )
        raise SystemExit(2)
    print(f"architecture asset verified: sha256={digest}")
    return ARCH_ASSET


ARCH = _verified_arch_asset()
ER_B = DESIGN / "tikz_er_b.png"


# ---- slide definitions -------------------------------------------------

slides: list[tuple[str, str]] = []  # (xml, rels)

# S1 封面
slides.append(
    (
        slide_xml(
            band(2, "鲜链智控", "冷链仓储智能监控与告警处置系统")
            + textbox(
                10,
                "cover",
                emu(0.9),
                emu(2.2),
                emu(11.5),
                emu(3.6),
                para(
                    run(
                        "两座冷库 · 三批苹果 · 十四个仿真设备的采集、告警、处置与双渠道通知",
                        2000,
                        color=BODY,
                    ),
                    space_after=400,
                )
                + para(
                    run(
                        "问题 → 方案 → 设备与数据 → 告警处置 → 通知 → 验证 → 限制",
                        1600,
                        color=SUB,
                    ),
                    space_after=400,
                )
                + para(
                    run(
                        "材料基准：共同 main 319aa63（= 一库 E1 实测来源 e7fffd61，全 Git 树一致）",
                        1300,
                        color=SUB,
                    ),
                    space_after=200,
                )
                + para(
                    run(
                        "分工：A 设备/架构/视频 · B 告警/通知/材料汇编 · C 数据/测试",
                        1300,
                        color=SUB,
                    ),
                    space_after=0,
                )
                + para(
                    run(
                        "注：通知终端实收为竞赛硬要求，当前状态见第 6 页，未完成项如实标注",
                        1100,
                        color=WARN,
                    ),
                    space_after=0,
                ),
            )
        ),
        slide_rels(LAYOUT_RID, {}),
    )
)

# S2 问题与需求
slides.append(
    (
        slide_xml(
            band(2, "问题与需求", "S2")
            + bullets(
                [
                    ("业务对象", INK, "1", 1600),
                    (
                        "• 两座苹果冷库、三批同品种苹果；软件模拟七个测区，每区一个温度采集身份 + 一个制冷执行身份，共 14 个逻辑设备",
                        BODY,
                        "0",
                        1400,
                    ),
                    (
                        "• 模拟数据不宣称真实果品品质实验；阈值异常不宣称真实腐败概率",
                        WARN,
                        "0",
                        1200,
                    ),
                    ("必选功能（竞赛通知要求）", INK, "1", 1600),
                    (
                        "• 设备注册/绑定/在线状态：心跳停发判离线、重连恢复、故障溯源",
                        BODY,
                        "0",
                        1400,
                    ),
                    (
                        "• 实时采集/清洗/历史/导出：MQTT 持续采样 → raw/质量 → 同筛选历史与 CSV",
                        BODY,
                        "0",
                        1400,
                    ),
                    (
                        "• 至少两种告警且可追溯：人工 ACK 处置、机器判定恢复、晚到坏证据更正",
                        BODY,
                        "0",
                        1400,
                    ),
                    (
                        "• 人工控制闭环：下发 → 执行 ACK → 后续温度反映效果（页面“发送”不算已执行）",
                        BODY,
                        "0",
                        1400,
                    ),
                    (
                        "• 通知硬要求：SMTP 与飞书手机端两个独立渠道分别终端实收；服务接受 ≠ 实收",
                        WARN,
                        "1",
                        1400,
                    ),
                    ("• 权限：普通用户只读、越权拒绝、重复操作幂等", BODY, "0", 1400),
                ],
                emu(0.6),
                emu(1.4),
                emu(12.1),
                emu(5.6),
            )
        ),
        slide_rels(LAYOUT_RID, {}),
    )
)

# S3 总体方案（架构图）
aw, ah = fit_png(ARCH, emu(9.2), emu(5.5))
slides.append(
    (
        slide_xml(
            band(2, "总体方案：分层架构", "S3")
            + picture(10, "arch", "Rarch100", emu(0.6), emu(1.35), aw, ah)
            + bullets(
                [
                    (
                        "展示层 React/TS · HTTP 层 FastAPI（统一 /api/v1，告警路由挂摄取门禁）",
                        BODY,
                        "0",
                        1200,
                    ),
                    (
                        "感知/执行层 14 仿真设备 · MQTT ACL · PostgreSQL 33 表（唯一迁移链 A03→B01→A04→A05→C01）",
                        BODY,
                        "0",
                        1200,
                    ),
                ],
                aw + emu(0.9),
                emu(1.6),
                SLIDE_W - aw - emu(1.3),
                emu(5.0),
            )
        ),
        slide_rels(LAYOUT_RID, {"Rarch100": "/ppt/media/current_architecture_e7.png"}),
    )
)

# S4 设备与数据
slides.append(
    (
        slide_xml(
            band(2, "设备与数据链", "S4")
            + bullets(
                [
                    ("设备链（A）", INK, "1", 1500),
                    (
                        "• 注册/绑定 → 心跳在线判定 → 控制命令持久化 → MQTT 下发 → 设备事务保存执行结果并返回原 ACK → 后续温度反映制冷",
                        BODY,
                        "0",
                        1300,
                    ),
                    (
                        "• 离线/故障/恢复事件关联追溯；逐设备账户与 ACL；真实部署默认 TLS",
                        BODY,
                        "0",
                        1300,
                    ),
                    ("数据链（A+C）", INK, "1", 1500),
                    (
                        "• 温度消息 → A 校验身份/绑定持久接纳 → C 同事务保存 raw/质量/观测",
                        BODY,
                        "0",
                        1300,
                    ),
                    (
                        "• 历史页面按快照分页，同筛选 CSV 完整 ID 对拍；重启后历史保留",
                        BODY,
                        "0",
                        1300,
                    ),
                    ("证据（A46，精确 62bb2ef 受控环境）", INK, "1", 1500),
                    (
                        "• 14 设备在线、7 采集流、7 执行 ACK 后降温；停发离线、同状态重启恢复并关联原离线事件；两库 CSV 行数一致（52/39）",
                        BODY,
                        "0",
                        1300,
                    ),
                    (
                        "• WH1 多批评价仍 hold；不是完整 E1，不写成已完成",
                        WARN,
                        "0",
                        1200,
                    ),
                ],
                emu(0.6),
                emu(1.4),
                emu(12.1),
                emu(5.6),
            )
        ),
        slide_rels(LAYOUT_RID, {}),
    )
)

# S5 告警生命周期与更正
bw, bh = fit_png(ER_B, emu(5.6), emu(4.9))
slides.append(
    (
        slide_xml(
            band(2, "告警生命周期与关联更正", "S5")
            + picture(10, "erb", "Rerb100", emu(0.5), emu(1.6), bw, bh)
            + bullets(
                [
                    ("事件与状态机（B）", INK, "1", 1400),
                    (
                        "• open → acknowledged（人工 ACK）→ resolved（机器判定恢复）；迁移表 append-only，无回退路径",
                        BODY,
                        "0",
                        1250,
                    ),
                    (
                        "• ACK 与恢复共用同一把原异常行锁（SELECT … FOR UPDATE），锁后重读；双确认只留一条、恢复不回退",
                        BODY,
                        "0",
                        1250,
                    ),
                    (
                        "• 恢复必须保留原异常的规则快照身份（rule_snapshot_id/rule_version/severity）",
                        BODY,
                        "0",
                        1250,
                    ),
                    ("幂等（五态回执）", INK, "1", 1400),
                    (
                        "• accepted_new / accepted_duplicate / conflict / invalid / retryable_failure；同 source 同摘要幂等，改正文冲突",
                        BODY,
                        "0",
                        1250,
                    ),
                    ("晚到坏证据更正（F03，PR #80）", INK, "1", 1400),
                    (
                        "• data_quality + related_source_event_id 指向原异常；同一把行锁；同库同批校验；零部分写入",
                        BODY,
                        "0",
                        1250,
                    ),
                    (
                        "• 更正永不写状态迁移：resolved 不回退、open 不重开、历史不改写；通知双 ID 可追溯",
                        BODY,
                        "0",
                        1250,
                    ),
                ],
                bw + emu(0.9),
                emu(1.4),
                SLIDE_W - bw - emu(1.5),
                emu(5.6),
            )
        ),
        slide_rels(LAYOUT_RID, {"Rerb100": "/ppt/media/tikz_er_b.png"}),
    )
)

# S6 通知派发
slides.append(
    (
        slide_xml(
            band(2, "通知派发与运行入口", "S6")
            + bullets(
                [
                    ("派发层（B）", INK, "1", 1500),
                    (
                        "• 只收集已配置渠道；有界重试（指数退避，permanent 不重试）；每次尝试落 alerts_notification_attempt 审计，审计失败不回滚业务事务",
                        BODY,
                        "0",
                        1300,
                    ),
                    (
                        "• 同 (事件，渠道) 已送达则跳过；去重查询失败保守 fail-closed 零发送；单渠道故障不影响另一渠道；只消费已提交事件",
                        BODY,
                        "0",
                        1300,
                    ),
                    ("持久 runner", INK, "1", 1500),
                    (
                        "• pending 扫描：缺 delivered 渠道 + 总尝试 < 12 + 最新尝试早于 60s（重启不清零的持久退避）；部分送达只补缺",
                        BODY,
                        "0",
                        1300,
                    ),
                    (
                        "• 单派发互斥：专用连接 PG advisory lock；第二实例零发送；连接丢失即停止认领；dry-run 零写零发",
                        BODY,
                        "0",
                        1300,
                    ),
                    ("运行入口（A notification_service.py）", INK, "1", 1500),
                    (
                        "• 显式操作员命令 + --send 开关，默认 dry-run；私有配置收件人；仅 PostgreSQL；SIGTERM 优雅停止；输出脱敏 JSON",
                        BODY,
                        "0",
                        1300,
                    ),
                    ("双渠道实收状态（如实）", WARN, "1", 1500),
                    (
                        "• 一库同版同事件 E1（#6 6459911129）：SMTP 与飞书各一次、两服务接受，队长本人确认邮件与手机飞书均收到、来源 ID 一致（d89c0bf9）",
                        WARN,
                        "0",
                        1300,
                    ),
                    (
                        "• 服务接受 ≠ 终端实收仍分记；旧 10-27 SSL EOF 为历史未补发；本段演示环境 dry-run，不展示发送画面",
                        WARN,
                        "0",
                        1300,
                    ),
                ],
                emu(0.6),
                emu(1.4),
                emu(12.1),
                emu(5.6),
            )
        ),
        slide_rels(LAYOUT_RID, {}),
    )
)

# S7 验证结果
slides.append(
    (
        slide_xml(
            band(2, "验证结果（真实数字，来源可溯）", "S7")
            + bullets(
                [
                    ("B 模块与全量回归", INK, "1", 1500),
                    (
                        "• 真实 PG 18 / Python 3.14 本机隔离实例（main 319aa63，= 一库 E1 实测来源 e7fffd61，全树一致）：告警与更正契约 21 条回归已随 main 合入",
                        BODY,
                        "0",
                        1350,
                    ),
                    (
                        "• PR #96 SMTP 阶段诊断为待审增量（候选历史，不并入冻结主线成绩）：告警套件 410 passed / 2 skipped / 15 subtests",
                        BODY,
                        "0",
                        1350,
                    ),
                    (
                        "• CI（PR #80 组合头 d1f5359）：9 workflows 全绿；mypy 0 错误；ruff 全绿",
                        BODY,
                        "0",
                        1350,
                    ),
                    ("A/C 组合证据", INK, "1", 1500),
                    (
                        "• 一库同版同事件 E1（#6 6459911129）：双渠道终端实收由队长本人确认；浏览器 281 行 14 列 CSV 与 15 页 ID 一致；普通用户 POST 403",
                        BODY,
                        "0",
                        1350,
                    ),
                    (
                        "• C 第二机（干净检出 e7fffd61）：默认 CMD 无源码挂载 HTTP 通过、14 设备夹具；HTTP CSV 10 行 14 列；API 文件 ≠ 浏览器下载（如实）",
                        BODY,
                        "0",
                        1350,
                    ),
                    (
                        "• A 离线入口（PR #95 cdb7d95）：14 在线设备、CSV 15 行全 ID、无网络 PG 重启回读 1202 条",
                        BODY,
                        "0",
                        1350,
                    ),
                    ("边界（如实）", WARN, "1", 1500),
                    (
                        "• 以上为隔离回归与受控分段，不是业务现场验收次数；第二机 180s sustained、浏览器下载核验、最终第二机/M01—M05 总签收未完成；旧 0db9402 失败与旧 10-27 SSL EOF 为有日期历史",
                        WARN,
                        "0",
                        1300,
                    ),
                ],
                emu(0.6),
                emu(1.4),
                emu(12.1),
                emu(5.6),
            )
        ),
        slide_rels(LAYOUT_RID, {}),
    )
)

# S8 限制与总结
slides.append(
    (
        slide_xml(
            band(2, "限制与团队贡献", "S8")
            + bullets(
                [
                    ("限制（不写成完成）", INK, "1", 1500),
                    (
                        "• 双渠道终端实收已由队长本人在一库同版 E1 确认（#6 6459911129）；E1 出口 ≠ 最终第二机总签收/投稿",
                        BODY,
                        "0",
                        1300,
                    ),
                    (
                        "• 智能算法未启用：版本化阈值规则为基线，无算法报告，不虚构性能页",
                        BODY,
                        "0",
                        1300,
                    ),
                    (
                        "• WH1 多批告警评价 hold；第二机自然持续恢复与浏览器下载核验未测；模拟数据不宣称真实品质实验",
                        BODY,
                        "0",
                        1300,
                    ),
                    ("团队分工与贡献", INK, "1", 1500),
                    (
                        "• A：设备注册/控制/执行闭环、系统架构与 ER、M01/M02 运行包、视频",
                        BODY,
                        "0",
                        1300,
                    ),
                    (
                        "• B：冷库/批次/版本化规则、告警生命周期（行锁串行化）、关联更正契约、通知派发与持久 runner、M03/M04 汇编",
                        BODY,
                        "0",
                        1300,
                    ),
                    (
                        "• C：采集/清洗/历史/快照、恢复 outbox 与门禁、团队测试报告底稿",
                        BODY,
                        "0",
                        1300,
                    ),
                    (
                        "• 辅助工具使用按赛事要求如实说明，不编造纯人工开发经历",
                        WARN,
                        "0",
                        1250,
                    ),
                ],
                emu(0.6),
                emu(1.4),
                emu(12.1),
                emu(5.6),
            )
        ),
        slide_rels(LAYOUT_RID, {}),
    )
)

# ---- package assembly --------------------------------------------------

ARGS = _parse_args()

OUT.parent.mkdir(parents=True, exist_ok=True)

src = _open_source(ARGS.input)


KEEP = {
    "ppt/theme/theme1.xml",
    "ppt/slideMasters/slideMaster1.xml",
    "ppt/slideMasters/_rels/slideMaster1.xml.rels",
    "ppt/slideMasters/theme/theme2.xml",
    "ppt/slideLayouts/slideLayout1.xml",
    "ppt/slideLayouts/slideLayout2.xml",
    "ppt/slideLayouts/slideLayout3.xml",
    "ppt/slideLayouts/slideLayout4.xml",
    "ppt/slideLayouts/slideLayout5.xml",
    "ppt/slideLayouts/slideLayout6.xml",
    "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
    "ppt/slideLayouts/_rels/slideLayout2.xml.rels",
    "ppt/slideLayouts/_rels/slideLayout3.xml.rels",
    "ppt/slideLayouts/_rels/slideLayout4.xml.rels",
    "ppt/slideLayouts/_rels/slideLayout5.xml.rels",
    "ppt/slideLayouts/_rels/slideLayout6.xml.rels",
    "ppt/presProps.xml",
    "ppt/tableStyles.xml",
    "_rels/.rels",
}
MEDIA = {
    "ppt/media/current_architecture_e7.png": ARCH.read_bytes(),
    "ppt/media/tikz_er_b.png": ER_B.read_bytes(),
}

# r namespace must be declared on p:presentation; rebuild it cleanly.
PRES_NS = (
    '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
)
presentation = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    + PRES_NS
    + '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="R0b7e91f218b247b0" /></p:sldMasterIdLst>'
    + "<p:sldIdLst>"
    + "".join(
        f'<p:sldId id="{256 + i}" r:id="Rsld{i:03d}" />' for i in range(len(slides))
    )
    + "</p:sldIdLst>"
    + f'<p:sldSz cx="{SLIDE_W}" cy="{SLIDE_H}" /><p:notesSz cx="6858000" cy="9144000" /></p:presentation>'
)

pres_rels = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="/ppt/theme/theme1.xml" Id="Ra28f1c5d519f4fdc" />'
    '<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="/ppt/slideMasters/slideMaster1.xml" Id="R0b7e91f218b247b0" />'
    '<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/presProps" Target="/ppt/presProps.xml" Id="R953de6ef23914f01" />'
    '<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/tableStyles" Target="/ppt/tableStyles.xml" Id="R7aae1da014314ad8" />'
    + "".join(
        f'<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="/ppt/slides/slide{i + 1}.xml" Id="Rsld{i:03d}" />'
        for i in range(len(slides))
    )
    + "</Relationships>"
)

ct = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml" />'
    '<Default Extension="png" ContentType="image/png" />'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" />'
    '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml" />'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml" />'
    '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml" />'
    '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml" />'
    '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml" />'
    '<Override PartName="/ppt/slideMasters/theme/theme2.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml" />'
    + "".join(
        f'<Override PartName="/ppt/slideLayouts/slideLayout{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml" />'
        for i in range(1, 7)
    )
    + '<Override PartName="/ppt/presProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presProps+xml" />'
    '<Override PartName="/ppt/tableStyles.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml" />'
    + "".join(
        f'<Override PartName="/ppt/slides/slide{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml" />'
        for i in range(len(slides))
    )
    + "</Types>"
)

app_xml = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<ap:Properties xmlns:ap="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
    "<ap:Application>FreshLink M04 builder</ap:Application>"
    "<ap:PresentationFormat>Widescreen</ap:PresentationFormat>"
    f"<ap:Slides>{len(slides)}</ap:Slides><ap:Notes>0</ap:Notes>"
    "<ap:HiddenSlides>0</ap:HiddenSlides><ap:SharedDoc>false</ap:SharedDoc><ap:DocSecurity>0</ap:DocSecurity>"
    "</ap:Properties>"
)
core_xml = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<coreProperties xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xmlns="http://schemas.openxmlformats.org/package/2006/metadata/core-properties">'
    "<dc:title>FreshLink M04 presentation draft (B)</dc:title>"
    "<dc:creator>xixidamei</dc:creator>"
    '<dcterms:created xsi:type="dcterms:W3CDTF">2026-10-25T00:00:00Z</dcterms:created>'
    "</coreProperties>"
)


def _zinfo(name: str) -> zipfile.ZipInfo:
    """Fixed-timestamp ZipInfo: two runs must produce identical bytes."""

    info = zipfile.ZipInfo(name, date_time=(2026, 10, 28, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for name in sorted(KEEP):
        z.writestr(_zinfo(name), src.read(name))
    for name, data in MEDIA.items():
        z.writestr(_zinfo(name), data)
    z.writestr(_zinfo("[Content_Types].xml"), ct)
    z.writestr(_zinfo("docProps/app.xml"), app_xml)
    z.writestr(_zinfo("docProps/core.xml"), core_xml)
    z.writestr(_zinfo("ppt/presentation.xml"), presentation)
    z.writestr(_zinfo("ppt/_rels/presentation.xml.rels"), pres_rels)
    for i, (xml, rels) in enumerate(slides):
        z.writestr(_zinfo(f"ppt/slides/slide{i + 1}.xml"), xml)
        z.writestr(_zinfo(f"ppt/slides/_rels/slide{i + 1}.xml.rels"), rels)

print(f"written {OUT} ({OUT.stat().st_size} bytes, {len(slides)} slides)")

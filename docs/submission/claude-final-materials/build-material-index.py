"""离线材料导航：显式根目录 + 相对文件清单 -> 离线 HTML 表格。

仅用 Python 标准库。只处理使用者显式给定的根目录和相对文件列表，生成离线 HTML 表格：
名称、来源、存在/缺失、字节数、实际 SHA256、相对链接。无网络/JS 外部依赖，不自动打开、
不修改输入、不递归扫目录、不搜索秘密、不按文件存在判定验收通过。

安全最小规则：
- 拒绝绝对路径、盘符、UNC、``..``、符号链接逃逸；
- 路径 HTML 转义、链接 URL 编码；
- 输出只写到显式指定的新输出位置，不覆盖输入清单/根目录文件；
- 缺文件如实标 missing，返回非零退出码并仍写可读报告；
- 只输出预先给定的文件名，不读取/打印任何文件正文或私密内容。

本工具仅帮助人找文件/查 hash，不承载 acceptance 状态，不替代原门禁、不宣称可参赛。
"""

import argparse
import hashlib
import html
import json
import os
import sys
from urllib.parse import quote

DISCLAIMER = (
    "本页由 build-material-index.py 生成，仅用于帮助定位文件和核对 SHA256，"
    "不承载验收状态、不替代原提交门禁、不宣称可参赛。文件存在且 hash 一致"
    "不代表业务、测试、实收或第二机已通过。"
)


def _norm_parts(rel: str):
    """把相对路径拆成规范化组件，便于逐项校验（不改变原字符串输出）。"""
    return rel.replace("\\", "/").split("/")


def is_absolute_like(rel: str) -> bool:
    """识别绝对路径、盘符、UNC 等显式绝对形态。"""
    if rel.startswith(("\\\\", "//")):
        return True
    # 盘符：C:/ 或 C:\\
    if len(rel) >= 2 and rel[1] == ":" and rel[0].isalpha():
        return True
    return bool(os.path.isabs(rel))


def validate_relative_path(rel: str) -> None:
    """校验使用者给定的相对路径；违规抛 ValueError。"""
    if not isinstance(rel, str) or rel == "":
        raise ValueError("路径为空")
    if "\x00" in rel:
        raise ValueError("路径含空字节")
    if is_absolute_like(rel):
        raise ValueError("拒绝绝对路径/盘符/UNC")
    parts = _norm_parts(rel)
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("拒绝 .. 组件")
        if part == "~":
            raise ValueError("拒绝 ~ 展开")
    # 去重后再次确认不含越界语义
    if any(p == ".." for p in parts if p != ""):
        raise ValueError("拒绝 .. 组件")


def safe_resolve(root: str, rel: str):
    """在 root 下安全解析 rel；返回绝对路径或 None（越界/符号链接逃逸）。"""
    validate_relative_path(rel)
    root_abs = os.path.abspath(root)
    candidate = os.path.abspath(os.path.join(root_abs, rel.replace("\\", "/")))
    root_real = os.path.realpath(root_abs)
    candidate_real = os.path.realpath(candidate)
    try:
        if os.path.commonpath([root_real, candidate_real]) != root_real:
            return None
    except ValueError:
        return None
    return candidate


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _cell(text: str) -> str:
    return html.escape(text)


def render_html(rows, out_abs: str, generated_meta: dict) -> str:
    out_dir = os.path.dirname(out_abs)
    lines = []
    lines.append("<!doctype html>")
    lines.append('<html lang="zh-CN"><head><meta charset="utf-8">')
    lines.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    lines.append("<title>材料文件与哈希清单</title>")
    lines.append(
        "<style>"
        "body{font-family:'Microsoft YaHei','Segoe UI',sans-serif;margin:24px;color:#0f172a;}"
        "h1{font-size:20px;} p.note{color:#b45309;max-width:920px;}"
        "table{border-collapse:collapse;width:100%;max-width:1120px;margin-top:12px;}"
        "th,td{border:1px solid #e2e8f0;padding:6px 8px;text-align:left;vertical-align:top;"
        "font-size:13px;word-break:break-all;}"
        "th{background:#1f2937;color:#fff;} tr.missing td{color:#b45309;}"
        "code{font-family:Consolas,monospace;font-size:12px;}"
        "</style></head><body>"
    )
    lines.append("<h1>材料文件与哈希清单</h1>")
    lines.append(f"<p class='note'>{_cell(DISCLAIMER)}</p>")
    if generated_meta:
        for k, v in generated_meta.items():
            lines.append(f"<p>生成信息：{_cell(k)} = <code>{_cell(str(v))}</code></p>")
    lines.append(
        "<table><thead><tr><th>名称</th><th>来源</th><th>存在/缺失</th>"
        "<th>字节数</th><th>SHA256</th><th>相对链接</th></tr></thead><tbody>"
    )
    for row in rows:
        name = row["path"]
        source = row.get("source", "")
        exists = row.get("exists", False)
        size = row.get("size")
        digest = row.get("sha256", "")
        status = "存在" if exists else "missing"
        tr_class = "" if exists else ' class="missing"'
        lines.append(f"<tr{tr_class}>")
        lines.append(f"<td>{_cell(name)}</td>")
        lines.append(f"<td>{_cell(source)}</td>")
        lines.append(f"<td>{status}</td>")
        lines.append(f"<td>{'' if size is None else size}</td>")
        lines.append(f"<td><code>{_cell(digest)}</code></td>")
        if exists:
            # 相对链接：从 HTML 输出所在目录指向文件
            target_abs = os.path.abspath(row["_abs"])
            link = os.path.relpath(target_abs, out_dir).replace("\\", "/")
            lines.append(f'<td><a href="{quote(link)}">打开</a></td>')
        else:
            lines.append("<td></td>")
        lines.append("</tr>")
    lines.append("</tbody></table></body></html>")
    return "\n".join(lines) + "\n"


def load_manifest(manifest_path: str):
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    files = data.get("files", [])
    if not isinstance(files, list):
        raise TypeError("manifest 的 files 必须是列表")
    result = []
    for item in files:
        if isinstance(item, str):
            result.append({"path": item, "source": ""})
        elif isinstance(item, dict):
            result.append({"path": item["path"], "source": item.get("source", "")})
        else:
            raise TypeError("files 项必须是字符串或对象")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="离线材料导航与文件/哈希清单")
    ap.add_argument("--root", required=True, help="文件相对路径的根目录")
    ap.add_argument("--manifest", required=True, help="显式相对文件清单 JSON")
    ap.add_argument("--out", required=True, help="输出 HTML 的新位置")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.root):
        print(f"root 不是目录：{args.root}", file=sys.stderr)
        return 2

    manifest_abs = os.path.abspath(args.manifest)
    out_abs = os.path.abspath(args.out)
    if os.path.lexists(out_abs):
        print("拒绝：输出文件已存在，不覆盖已有成果", file=sys.stderr)
        return 2
    if out_abs == manifest_abs:
        print("拒绝：输出位置覆盖输入清单", file=sys.stderr)
        return 2

    try:
        entries = load_manifest(manifest_abs)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as e:
        print(f"读取清单失败：{e}", file=sys.stderr)
        return 2

    rows = []
    missing = 0
    rejected = 0
    for entry in entries:
        rel = entry["path"]
        try:
            target = safe_resolve(args.root, rel)
        except ValueError as e:
            print(f"拒绝路径 {rel!r}：{e}", file=sys.stderr)
            rows.append(
                {
                    "path": rel,
                    "source": entry.get("source", ""),
                    "exists": False,
                    "size": None,
                    "sha256": "",
                    "error": str(e),
                }
            )
            rejected += 1
            continue
        if target is None:
            print(f"拒绝路径 {rel!r}：符号链接越界或解析失败", file=sys.stderr)
            rows.append(
                {
                    "path": rel,
                    "source": entry.get("source", ""),
                    "exists": False,
                    "size": None,
                    "sha256": "",
                    "error": "越界",
                }
            )
            rejected += 1
            continue
        if os.path.isfile(target):
            size = os.path.getsize(target)
            digest = sha256_of(target)
            rows.append(
                {
                    "path": rel,
                    "source": entry.get("source", ""),
                    "exists": True,
                    "size": size,
                    "sha256": digest,
                    "_abs": target,
                }
            )
        else:
            missing += 1
            print(f"缺失文件：{rel}", file=sys.stderr)
            rows.append(
                {
                    "path": rel,
                    "source": entry.get("source", ""),
                    "exists": False,
                    "size": None,
                    "sha256": "",
                }
            )

    try:
        root_display = os.path.relpath(args.root, os.getcwd())
    except ValueError:
        root_display = os.path.abspath(args.root)
    meta = {
        "字节口径": "对显式根目录中的实际文件逐字节计算，不做换行或文本规范化",
        "检出约束": "本目录文本由 .gitattributes 固定 LF；复制或重新打包后仍须按实际字节重生清单",
        "root": root_display,
        "总数": len(rows),
        "存在": sum(1 for r in rows if r.get("exists")),
        "缺失": missing,
        "拒绝": rejected,
    }
    html_text = render_html(rows, out_abs, meta)
    out_dir = os.path.dirname(out_abs)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    with open(out_abs, "x", encoding="utf-8") as f:
        f.write(html_text)
    print(f"HTML 已写入：{out_abs}")
    print(
        f"存在 {meta['存在']} / 缺失 {missing} / 拒绝 {rejected}（共 {meta['总数']}）"
    )

    if missing or rejected:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

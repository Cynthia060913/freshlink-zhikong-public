#!/usr/bin/env python3
"""build-material-index.py 的必要小测试（只用标准库，在临时目录实跑）。

覆盖：
1. 合法含中文+空格路径，SHA256 与实际一致；
2. 缺失文件如实 missing 并返回非零，但仍写可读 HTML；
3. 越界（..）、绝对/盘符/UNC、符号链接逃逸被拒绝；
4. 路径中的 HTML 特殊字符被转义（< > &）。

不引入依赖，不测试整个业务。运行：
    python test-material-index.py
"""

import hashlib
import html
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build-material-index.py")


def run_build(root, manifest, out):
    return subprocess.run(
        [sys.executable, BUILD, "--root", root, "--manifest", manifest, "--out", out],
        capture_output=True, text=True,
    )


def test_legal_chinese_space_hash():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "root")
        os.makedirs(root)
        fname = "架构 图（中文与空格）.png"
        content = b"hello freshlink\x00\x01\x02"
        fpath = os.path.join(root, fname)
        with open(fpath, "wb") as f:
            f.write(content)
        manifest = os.path.join(tmp, "m.json")
        with open(manifest, "w", encoding="utf-8") as f:
            f.write('{"files":[{"path":"' + fname + '","source":"s"}]}')
        out = os.path.join(tmp, "out", "index.html")
        r = run_build(root, manifest, out)
        assert r.returncode == 0, r.stderr
        html_text = open(out, encoding="utf-8").read()
        expect = hashlib.sha256(content).hexdigest()
        assert expect in html_text, "sha256 未写入"
        assert "存在" in html_text
        print("PASS 合法中文+空格路径与 hash")


def test_missing_returns_nonzero_but_writes_html():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "root")
        os.makedirs(root)
        manifest = os.path.join(tmp, "m.json")
        with open(manifest, "w", encoding="utf-8") as f:
            f.write('{"files":[{"path":"not-there.bin","source":"x"}]}')
        out = os.path.join(tmp, "index.html")
        r = run_build(root, manifest, out)
        assert r.returncode != 0, "缺失文件应返回非零"
        html_text = open(out, encoding="utf-8").read()
        assert "missing" in html_text
        assert "not-there.bin" in html_text
        print("PASS 缺失文件非零退出且仍写报告")


def test_reject_traversal_and_abs():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "root")
        os.makedirs(root)
        for bad in ("../escape.txt", "C:/abs.txt", "\\\\server\\share\\x", "a/../../x"):
            manifest = os.path.join(tmp, "m.json")
            with open(manifest, "w", encoding="utf-8") as f:
                f.write('{"files":[{"path":"' + bad.replace("\\", "\\\\") + '"}]}')
            out = os.path.join(tmp, "o.html")
            r = run_build(root, manifest, out)
            assert r.returncode != 0, f"应拒绝 {bad!r}"
        print("PASS 越界/绝对/盘符/UNC 拒绝")


def test_html_escape():
    # Windows 文件名不允许 < > &，故用 source 标签承载 HTML 特殊字符来验证转义。
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "root")
        os.makedirs(root)
        fname = "normal-name.txt"
        fpath = os.path.join(root, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write("x")
        manifest = os.path.join(tmp, "m.json")
        with open(manifest, "w", encoding="utf-8") as f:
            f.write('{"files":[{"path":"' + fname + '","source":"s<&>"}]}')
        out = os.path.join(tmp, "index.html")
        r = run_build(root, manifest, out)
        assert r.returncode == 0, r.stderr
        html_text = open(out, encoding="utf-8").read()
        assert html.escape("s<&>") in html_text
        assert "s<&>" not in html_text  # 原文未转义形式不应出现
        print("PASS HTML 特殊字符转义")


def test_existing_output_and_source_are_never_overwritten():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "root")
        os.makedirs(root)
        source = os.path.join(root, "keep.txt")
        with open(source, "w", encoding="utf-8") as f:
            f.write("ORIGINAL_KEEP")
        manifest = os.path.join(tmp, "m.json")
        with open(manifest, "w", encoding="utf-8") as f:
            f.write('{"files":[{"path":"keep.txt"}]}')
        result = run_build(root, manifest, source)
        assert result.returncode == 2
        with open(source, encoding="utf-8") as f:
            assert f.read() == "ORIGINAL_KEEP"
        output = os.path.join(tmp, "existing.html")
        with open(output, "w", encoding="utf-8") as f:
            f.write("EXISTING_REPORT")
        assert run_build(root, manifest, output).returncode == 2
        with open(output, encoding="utf-8") as f:
            assert f.read() == "EXISTING_REPORT"
        print("PASS 已有输入/输出零覆盖")


if __name__ == "__main__":
    test_legal_chinese_space_hash()
    test_missing_returns_nonzero_but_writes_html()
    test_reject_traversal_and_abs()
    test_html_escape()
    test_existing_output_and_source_are_never_overwritten()
    print("ALL TESTS PASSED")

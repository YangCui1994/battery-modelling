"""通过 GitHub Git Data API 推送本地仓库内容。

背景：本机全局 git 配置把 github.com 重写到 gh-proxy.com，而该代理不中继
git 的 smart-HTTP 协议（push/ls-remote 都会挂住）。但 `gh api` 的控制通道是通的，
所以改用 Git Data API（blob -> tree -> commit -> ref）直接建提交。

用法::

    python tools/gh_push.py            # 预览将要推送的文件
    python tools/gh_push.py --apply    # 真正推送
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

OWNER_REPO = "YangCui1994/battery-modelling"
BRANCH = "main"
MESSAGE_FILE = ".git/COMMIT_EDITMSG"


def gh_api(args: list[str], data: dict | None = None) -> dict:
    """调用 gh api，返回解析后的 JSON。"""
    cmd = ["gh", "api"] + args
    tmp = None
    if data is not None:
        fd, tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)                     # Windows: 必须先关掉裸 fd，否则文件被锁
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        cmd += ["--input", tmp]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    finally:
        if tmp:
            try:
                Path(tmp).unlink(missing_ok=True)
            except PermissionError:
                pass                     # 清理失败不影响推送结果
    if proc.returncode != 0:
        raise RuntimeError(f"gh api 失败: {' '.join(cmd[-3:])}\n{proc.stderr[:500]}")
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True).stdout
    return [f.decode("utf-8", "surrogateescape") for f in out.split(b"\0") if f]


def commit_message() -> str:
    msg = subprocess.run(["git", "log", "-1", "--pretty=%B"],
                         capture_output=True, text=True, encoding="utf-8").stdout.strip()
    return msg or "Initial commit"


def ensure_bootstrapped() -> None:
    """空仓库不能建 blob（HTTP 409）。先用 Contents API 写入一个占位提交。"""
    proc = subprocess.run(
        ["gh", "api", f"/repos/{OWNER_REPO}/contents/.bootstrap"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode == 0:
        return  # 仓库已有内容
    content = base64.b64encode(b"bootstrap\n").decode("ascii")
    gh_api(
        [f"/repos/{OWNER_REPO}/contents/.bootstrap", "-X", "PUT"],
        {"message": "Bootstrap repository", "content": content, "branch": BRANCH},
    )
    print("已用 Contents API 做引导提交")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正推送（默认只预览）")
    args = ap.parse_args()

    files = tracked_files()
    print(f"待推送 {len(files)} 个文件，分支 {BRANCH}")

    if not args.apply:
        for f in files:
            print(f"  {f}")
        print("\n预览模式。加 --apply 真正推送。")
        return 0

    ensure_bootstrapped()

    # 1) 每个文件建 blob
    tree_items = []
    for i, rel in enumerate(files, 1):
        data = (Path.cwd() / rel).read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        blobs = gh_api(
            [f"/repos/{OWNER_REPO}/git/blobs"],
            {"content": b64, "encoding": "base64"},
        )
        tree_items.append({"path": rel, "mode": "100644", "type": "blob", "sha": blobs["sha"]})
        print(f"  [{i}/{len(files)}] blob {blobs['sha'][:8]}  {rel}")

    # 2) 建 tree
    tree = gh_api([f"/repos/{OWNER_REPO}/git/trees"], {"tree": tree_items})
    print(f"tree {tree['sha'][:8]}")

    # 3) 建 commit（空仓库：无 parent）
    commit = gh_api(
        [f"/repos/{OWNER_REPO}/git/commits"],
        {"message": commit_message(), "tree": tree["sha"], "parents": []},
    )
    print(f"commit {commit['sha'][:8]}")

    # 4) 建/更新分支引用
    try:
        gh_api([f"/repos/{OWNER_REPO}/git/refs"],
               {"ref": f"refs/heads/{BRANCH}", "sha": commit["sha"]})
        print(f"created ref refs/heads/{BRANCH}")
    except RuntimeError:
        gh_api([f"/repos/{OWNER_REPO}/git/refs/heads/{BRANCH}"],
               {"sha": commit["sha"], "force": True}, )
        print(f"updated ref refs/heads/{BRANCH}")

    print(f"\n完成: https://github.com/{OWNER_REPO}/tree/{BRANCH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

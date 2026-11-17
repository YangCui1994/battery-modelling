"""把本地提交推到 GitHub（本机网络环境专用）。

本机全局 git 配置把 github.com 重写到 gh-proxy.com，而该代理不中继 git 的
smart-HTTP 协议（push / ls-remote 会挂住）。但本机有本地代理 127.0.0.1:7890，
直连 github.com 是通的。因此这里显式带 token + 本地代理推送。

用法::

    python tools/gh_push.py            # 预览：显示本地/远端差异
    python tools/gh_push.py --apply    # 真正推送
"""
from __future__ import annotations

import argparse
import subprocess
import sys

REPO = "https://github.com/YangCui1994/battery-modelling.git"
PROXY = "http://127.0.0.1:7890"


def sh(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", **kw)


def token() -> str:
    out = sh(["gh", "auth", "token"]).stdout.strip()
    if not out:
        raise SystemExit("拿不到 gh token，请先 gh auth login")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true", help="强制推送（历史被重写时）")
    args = ap.parse_args()

    local = sh(["git", "rev-parse", "HEAD"]).stdout.strip()
    print(f"local HEAD : {local[:12]}")

    if not args.apply:
        log = sh(["git", "log", "--oneline", "-5"]).stdout
        print(log)
        print("预览模式，加 --apply 推送")
        return 0

    url = REPO.replace("https://", f"https://x-access-token:{token()}@")
    cmd = ["git", "-c", "credential.helper=",
           "-c", f"http.proxy={PROXY}", "-c", f"https.proxy={PROXY}",
           "push", url, "HEAD:refs/heads/main"]
    if args.force:
        cmd.insert(-1, "--force")

    proc = sh(cmd)
    print(proc.stdout.strip() or proc.stderr.strip())
    if proc.returncode != 0:
        print("推送失败")
        return proc.returncode
    print("\n完成: https://github.com/YangCui1994/battery-modelling")
    return 0


if __name__ == "__main__":
    sys.exit(main())

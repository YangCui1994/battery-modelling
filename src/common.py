"""仓库内共用的小工具：定位输出目录、统一 matplotlib 中文字体。

所有脚本的图都写到仓库根的 results/ 下，而不是当前工作目录，
这样从任意目录运行都能找到产物。
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "results"


def results_dir() -> Path:
    """返回 results/ 绝对路径，不存在则创建。"""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR


def savefig(fig, name: str) -> Path:
    """把图存到 results/<name> 并返回路径。"""
    path = results_dir() / name
    fig.savefig(path)
    return path


def setup_matplotlib() -> None:
    """统一中文字体，避免 Windows 上出现方块。"""
    import matplotlib

    matplotlib.use("Agg")
    matplotlib.rcParams["font.sans-serif"] = [
        "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"
    ]
    matplotlib.rcParams["axes.unicode_minus"] = False


def polidemo_exe() -> Path:
    """POLIDEMO.exe 的相对位置（可用环境变量 POLIDEMO_EXE 覆盖）。"""
    env = os.environ.get("POLIDEMO_EXE")
    if env:
        return Path(env)
    return REPO_ROOT / "POLIDEMO-windows" / "POLIDEMO.exe"

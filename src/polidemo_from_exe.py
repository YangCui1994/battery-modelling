"""从打包好的 POLIDEMO.exe 里直接加载 polidemo 包（PyInstaller 字节码），无需反编译。

用法::

    import polidemo_from_exe
    polidemo_from_exe.load(r"POLIDEMO-windows\\POLIDEMO.exe")   # 注册 polidemo.* 到 sys.modules
    from polidemo import ModelParams, init_aging_state, run_cycles

    p = ModelParams()
    recs = run_cycles(p, init_aging_state(p), 1, 1)
    print(recs[-1].capacity_Ah)      # 约 20.2453 Ah

依赖：pyinstaller（只用到它的 archive reader）、numpy、scipy、pandas。
"""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

MODULES = [
    "polidemo.params",
    "polidemo.data",
    "polidemo.materials",
    "polidemo.ocv",
    "polidemo.aging",
    "polidemo.mechanics",
    "polidemo.spm",
    "polidemo.runner",
    "polidemo.export",
    "polidemo",
]


def load(exe: str | Path | None = None, *, reload: bool = False):
    """把 exe 里的 polidemo 包注册进 sys.modules 并返回顶层包。

    exe 省略时用 common.polidemo_exe()（仓库内 POLIDEMO-windows/，或环境变量 POLIDEMO_EXE）。
    """
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    if "polidemo" in sys.modules and not reload:
        return sys.modules["polidemo"]

    if exe is None:
        from common import polidemo_exe
        exe = polidemo_exe()
    exe = Path(exe).resolve()
    archive = CArchiveReader(str(exe))
    with tempfile.TemporaryDirectory() as tmp:
        pyz_path = Path(tmp) / "PYZ.pyz"
        pyz_path.write_bytes(archive.extract("PYZ.pyz"))
        pyz = ZlibArchiveReader(str(pyz_path), check_pymagic=False)
        return _register(pyz)


def _register(pyz):
    mods = {}
    for name in MODULES:
        mod = types.ModuleType(name)
        mod.__file__ = name.replace(".", "/") + ".py"
        if name == "polidemo":
            mod.__package__ = name
            mod.__path__ = []
        else:
            mod.__package__ = name.rpartition(".")[0]
        sys.modules[name] = mod
        mods[name] = mod

    for name in MODULES:
        exec(pyz.extract(name), mods[name].__dict__)
    return mods["polidemo"]


if __name__ == "__main__":
    # 自检：与 `POLIDEMO.exe --selftest` 对标（20.2453 Ah / 0.00034 mm / 11150.2 s）
    load()
    from polidemo import ModelParams, init_aging_state, run_cycles

    p = ModelParams()
    rec = run_cycles(p, init_aging_state(p), 1, 1)[-1]
    print(f"capacity {rec.capacity_Ah:.4f} Ah | irr. swelling {rec.irr_swelling:.6f} mm | time {rec.total_time:.1f} s")
    assert abs(rec.capacity_Ah - 20.2453) < 1e-3, "与官方自检容量不一致"
    assert abs(rec.total_time - 11150.2) < 1.0, "与官方自检时长不一致"
    print("OK")

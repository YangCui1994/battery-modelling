"""环境自检：确认装对了、2D 网格能用、PyBaMM 能真跑。

    python verify.py

全部通过时打印 ALL CHECKS PASSED；任一项失败返回非零退出码。
"""
from __future__ import annotations

import sys
import traceback

CHECKS: list[tuple[str, object]] = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


@check("Python 版本 >= 3.10")
def _python():
    assert sys.version_info >= (3, 10), f"需要 3.10+，当前 {sys.version.split()[0]}"


@check("导入 pybamm 并打印版本")
def _pybamm():
    import pybamm
    print(f"    pybamm {pybamm.__version__}")


@check("导入 matplotlib")
def _mpl():
    import matplotlib
    print(f"    matplotlib {matplotlib.__version__}")


@check("scikit-fem 可用（2D 必需）")
def _skfem():
    import skfem
    print(f"    scikit-fem {skfem.__version__}")


@check("一次真实 SPM 求解")
def _spm():
    import numpy as np
    import pybamm

    pybamm.set_logging_level("ERROR")
    model = pybamm.lithium_ion.SPM()
    sim = pybamm.Simulation(model, C_rate=1,
                            parameter_values=pybamm.ParameterValues("Chen2020"),
                            solver=pybamm.IDAKLUSolver())
    sol = sim.solve([0, 600])
    V = float(np.ravel(sol["Voltage [V]"].entries)[-1])
    assert 2.5 < V < 4.3, f"电压不合理: {V}"
    print(f"    V(600s) = {V:.3f} V")


@check("2D 软包网格 + 求解（需 scikit-fem）")
def _pouch2d():
    import numpy as np
    import pybamm

    pybamm.set_logging_level("ERROR")
    model = pybamm.lithium_ion.SPMe({
        "thermal": "x-lumped", "cell geometry": "pouch",
        "current collector": "potential pair", "dimensionality": 2,
    })
    sim = pybamm.Simulation(model,
                            parameter_values=pybamm.ParameterValues("Marquis2019"),
                            solver=pybamm.IDAKLUSolver())
    sol = sim.solve([0, 120])
    T = np.ravel(sol["X-averaged cell temperature [C]"].entries)
    print(f"    2D 求解 OK, T_end = {T[-1]:.2f} °C")


@check("results/ 可写")
def _results():
    import common

    p = common.results_dir()
    probe = p / ".write_test"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    print(f"    {p}")


def main() -> int:
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

    print("=" * 64)
    print("环境自检")
    print("=" * 64)
    failed = 0
    for name, fn in CHECKS:
        print(f"[..] {name}")
        try:
            fn()
            print(f"[OK] {name}")
        except Exception as exc:                                  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
            if os.environ.get("VERIFY_DEBUG"):
                traceback.print_exc()

    print("=" * 64)
    if failed:
        print(f"{failed} 项失败")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

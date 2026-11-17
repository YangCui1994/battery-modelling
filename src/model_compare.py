"""模型层级对比：SPM / SPMe / DFN 的精度与代价。

用法::

    python src/model_compare.py [C_rate] [时长s]

输出：results/model_compare_{C}C.png，以及终端里的用时/末端电压/末端温度表。

注意：本脚本用 Chen2020（2.3 Ah 级），2C 恒流放电到 2.5 V。
"""
from __future__ import annotations

import sys
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pybamm  # noqa: E402

import common  # noqa: E402

common.setup_matplotlib()
pybamm.set_logging_level("ERROR")

C_RATE = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
T_END = float(sys.argv[2]) if len(sys.argv) > 2 else 1800.0
PARAM_SET = "Chen2020"
MODELS = [("SPM", pybamm.lithium_ion.SPM),
          ("SPMe", pybamm.lithium_ion.SPMe),
          ("DFN", pybamm.lithium_ion.DFN)]


def run(name, cls):
    model = cls({"thermal": "lumped"})
    pv = pybamm.ParameterValues(PARAM_SET)
    sim = pybamm.Simulation(model, C_rate=C_RATE, parameter_values=pv,
                            solver=pybamm.IDAKLUSolver())
    t0 = time.time()
    sol = sim.solve([0, T_END])
    wall = time.time() - t0

    t = np.ravel(sol["Time [s]"].entries)
    V = np.ravel(sol["Voltage [V]"].entries)
    # 厚度方向有多个节点，取 x 平均才是标量温度
    T = np.ravel(sol["X-averaged cell temperature [C]"].entries)
    return {"name": name, "wall": wall, "t": t, "V": V, "T": T,
            "V_end": float(V[-1]), "T_end": float(T[-1]),
            "hit": float(t[np.argmax(V <= 2.5)]) if np.any(V <= 2.5) else float("nan")}


def main():
    print("=" * 72)
    print(f"模型层级对比  参数={PARAM_SET}  {C_RATE:g}C  放电 {T_END:g}s")
    print("=" * 72)

    rows = [run(n, c) for n, c in MODELS]
    print(f"\n{'模型':<6} {'用时':>8} {'末端电压':>10} {'末端温度':>10} {'到2.5V':>10}")
    for r in rows:
        print(f"{r['name']:<6} {r['wall']:>7.2f}s {r['V_end']:>9.3f}V "
              f"{r['T_end']:>8.1f}C {r['hit']:>8.0f}s")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4), dpi=130)
    for r in rows:
        axes[0].plot(r["t"], r["V"], label=r["name"])
        axes[1].plot(r["t"], r["T"], label=r["name"])
    axes[0].axhline(2.5, ls=":", c="k", lw=1)
    axes[0].set(xlabel="t [s]", ylabel="电压 [V]", title=f"{C_RATE:g}C 放电曲线")
    axes[1].set(xlabel="t [s]", ylabel="温度 [°C]", title="X 平均温度")
    for a in axes:
        a.legend()
        a.grid(alpha=.3)
    fig.tight_layout()
    path = common.savefig(fig, f"model_compare_{C_RATE:g}C.png")
    print(f"\nsaved {path}")


if __name__ == "__main__":
    main()

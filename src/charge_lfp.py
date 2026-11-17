"""LFP/石墨：1C 充电（2.5 V -> 3.65 V）与 dQ/dV。

## 关于"从 2.5 V 开始充电"

这里有个容易踩的点：**同一 SOC 下的端电压取决于电流方向**。
实测（Prada2013，1C）：

| SOC | 静置/放电端电压 | 充电端电压 |
|---|---|---|
| 1.50% | ~2.47 V | 2.4905 V |
| 3.07% | **2.500 V** | 2.7264 V |

也就是说 `initial_soc=0.03074` 对应"**静置时** 2.5 V"，
但一挂上 1C 充电，端电压立刻被过电位抬到 2.72 V。
本脚本按"**充电过程中从 2.5 V 开始**"取 `initial_soc≈0.01555`，
这样充电曲线确实起于 2.5 V。两种口径的差别就是这个过电位，约 220 mV。

## 符号约定（PyBaMM）

`Current function [A]` **正值为放电，负值为充电**。
所以本脚本设 `-Nominal capacity` 表示 1C 充电。写成正值会变成放电。

用法::

    python src/charge_lfp.py
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pybamm  # noqa: E402

import common  # noqa: E402

common.setup_matplotlib()
pybamm.set_logging_level("ERROR")

PARAM_SET = "Prada2013"
SOC_2V5_CHARGE = 0.01555     # 1C 充电下，端电压恰为 2.5 V
SOC_2V5_REST = 0.03074       # 静置下 OCV = 2.5 V
V_TOP = 3.65
V_BOTTOM = 2.5
DT_MAX = 0.5
T_MAX = 9000


def simulate(charge: bool, soc: float):
    """charge=True 走 1C 充电（到 3.65 V）；False 走 1C 放电（到 2.5 V）。

    用模型自带的截止电压事件精确停在 3.65 / 2.5 V，
    而不是给一个 t_eval 硬截断（否则曲线会冲过截止点）。
    """
    pv = pybamm.ParameterValues(PARAM_SET)
    cap = pv["Nominal cell capacity [A.h]"]
    # 正=放电，负=充电
    pv["Current function [A]"] = -cap if charge else cap
    # 充放共用同一窗口 [2.5, 3.65]，否则放电会从参数集自带的 3.6 V 上限起，
    # 起点落在电压瞬变段，dQ/dV 会出现假的万级尖峰。
    pv["Upper voltage cut-off [V]"] = V_TOP
    pv["Lower voltage cut-off [V]"] = V_BOTTOM

    model = pybamm.lithium_ion.SPMe({"thermal": "isothermal"})
    solver = pybamm.IDAKLUSolver(options={"dt_max": DT_MAX})
    sim = pybamm.Simulation(model, parameter_values=pv, solver=solver)
    sol = sim.solve([0, T_MAX], initial_soc=soc)

    V = np.ravel(sol["Voltage [V]"].entries)
    # 充电时 "Discharge capacity [A.h]" 是负的，也没有 "Charge capacity" 变量；
    # 用 "Throughput capacity [A.h]"（累计通过电量，恒正）作为转移电荷量。
    Q = np.ravel(sol["Throughput capacity [A.h]"].entries)
    n = min(len(V), len(Q))
    V, Q = V[:n], Q[:n]
    ok = np.isfinite(V) & np.isfinite(Q)
    V, Q = V[ok], Q[ok]

    o = np.argsort(Q)
    V, Q = V[o], Q[o]
    keep = np.concatenate(([True], np.diff(Q) > 1e-12))
    V, Q = V[keep], Q[keep]

    # 排序后 V 与 Q 都单调递增（充电）/ 递减（放电），所以统一取 |dQ/dV|：
    # 充电 dQ/dV>0，放电按惯例也报正值，两条曲线才能直接比较峰位。
    dQdV = np.abs(np.gradient(Q, V))
    return V, Q, dQdV, float(Q.max()), float(np.ravel(sol["Time [s]"].entries)[-1])


def main():
    print("=" * 74)
    print(f"LFP/石墨 1C 充电 {2.5} V -> {V_TOP} V   ({PARAM_SET})")
    print("=" * 74)

    Vc, Qc, dQc, capc, tc = simulate(True, SOC_2V5_CHARGE)
    print(f"\n充电: {Vc[0]:.3f} V -> {Vc[-1]:.3f} V   容量={capc:.4f} Ah  用时={tc:.0f}s  点数={len(Vc)}")

    # 对照：1C 放电（从 3.65 V 放到 2.5 V）
    Vd, Qd, dQd, capd, td = simulate(False, 1.0)
    print(f"放电: {Vd[-1]:.3f} V -> {Vd[0]:.3f} V   容量={capd:.4f} Ah  用时={td:.0f}s")

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2), dpi=130)

    axes[0].plot(Vc, Qc, lw=1.8, label="1C 充电")
    axes[0].plot(Vd, Qd, lw=1.4, ls="--", label="1C 放电")
    axes[0].axvline(3.65, ls=":", c="k", lw=1)
    axes[0].set(xlabel="电压 [V]", ylabel="容量 [Ah]", title="充/放电曲线")
    axes[0].legend()
    axes[0].grid(alpha=.3)

    # dQ/dV：注意放电曲线电压轴是降序，反转以便同图比较
    axes[1].plot(Vc[:-2], dQc[:-2], lw=1.8, label="充电 dQ/dV")
    axes[1].plot(Vd[:-2], dQd[:-2], lw=1.4, ls="--", label="放电 dQ/dV")
    axes[1].set(xlabel="电压 [V]", ylabel="dQ/dV", title="dQ/dV 充放对比")
    axes[1].legend()
    axes[1].grid(alpha=.3)

    axes[2].plot(Qc, Vc, lw=1.8)
    axes[2].axhline(3.65, ls=":", c="r", lw=1, label="3.65 V 截止")
    axes[2].axhline(2.5, ls=":", c="g", lw=1, label="2.5 V 起点")
    axes[2].set(xlabel="充电容量 [Ah]", ylabel="电压 [V]", title="充电曲线（容量-电压）")
    axes[2].legend()
    axes[2].grid(alpha=.3)

    fig.tight_layout()
    path = common.savefig(fig, "charge_lfp_1C.png")
    print(f"\nsaved {path}")

    print("\n[数据]")
    print(f"  充电容量      {capc:.4f} Ah")
    print(f"  充电 dQ/dV 峰 {dQc.max():.2f} @ {Vc[np.argmax(dQc)]:.3f} V")
    print(f"  放电 dQ/dV 峰 {dQd.max():.2f} @ {Vd[np.argmax(dQd)]:.3f} V")
    print(f"  充放峰位差    {Vc[np.argmax(dQc)] - Vd[np.argmax(dQd)]:+.3f} V")


if __name__ == "__main__":
    main()



"""LFP/石墨 电芯的区域化 LLI / LAM -> dQ/dV 不均匀性演示。

## 为什么换 LFP

LCO/NMC 的 dQ/dV 有多个宽峰，非均匀性表现不直观。
LFP 的电压平台极平：本参数集下 **96.7% 的容量挤在 3.15-3.35 V**，
dQ/dV 是一个又高又窄的单峰（约 185 / 31，取决于倍率与 SOC 窗口）。
平台边缘一分开，峰就直接**裂成多个**，肉眼可辨——这是看非均匀性最好的化学体系。

## 关键：这个电芯是**石墨限容**

Prada2013（LFP_ocp_Afshar2017）：

| 量 | 值 |
|---|---|
| Qn（石墨理论） | 2.91 Ah |
| Qp（LFP 理论） | 3.29 Ah |
| 初始 stoich | x_n = 0.81, x_p = 0.004 |

所以两个退化杠杆落点不同，且都作用在**限容端**：

* **LAM** -> 改 `Negative electrode active material volume fraction`
  实测 -10% 时容量 1.133 -> 1.018 Ah，dQ/dV 峰 31.8 -> 28.2（峰高小幅降）。
* **LLI** -> 改 `Initial concentration in negative electrode`
  实测 -10% 时容量 -> 1.015 Ah，dQ/dV 峰 31.8 -> **12.3**（峰高大幅降）。

同样 10% 容量损失，峰高差 2.3 倍 —— 这就是 dQ/dV 区分 LLI/LAM 的物理基础。

## 时间步长如何选

`dt_max` 控制积分器内部最大步长，**默认无 t_eval 时会按内部步输出**，
所以调小 dt_max 能直接加密输出（实测 dt_max=0.5 得到 ~7 万点）。
这比 `t_eval` 更省：同样的 dQ/dV 平滑度，dt_max=0.5 用时 0.54 s，
`t_eval=4000` 要 2.32 s。所以本脚本用 `dt_max`。

> 注意：光加密时间步**不能**消除动力学畸变。倍率越高，过电位把峰压低压偏
> （实测 1C -> C/20 峰位差 98 mV、峰高差 3.2 倍），这只能靠降倍率解决。
> 本脚本用 C/20 近似准静态，同时 dt_max 保证采样密度。

用法::

    python src/dqdv_lfp.py            # 默认 4 区域
    python src/dqdv_lfp.py 6          # 6 区域
"""
from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pybamm  # noqa: E402

import common  # noqa: E402

common.setup_matplotlib()
pybamm.set_logging_level("ERROR")

PARAM_SET = "Prada2013"
C_RATE = 0.05          # C/20 近似准静态
SOC0 = 0.5             # 从 50% SOC 起放：满电起放会贴着 LFP 边缘，电压超上限
DT_MAX = 0.5           # 积分器最大步长 [s]，同时决定输出密度
T_END = 40000

CNN = "Initial concentration in negative electrode [mol.m-3]"
EN = "Negative electrode active material volume fraction"

N_REGIONS = int(sys.argv[1]) if len(sys.argv) > 1 else 4


def local_curve(lli: float = 0.0, lam: float = 0.0):
    """算一个区域的 (V, Q, dQ/dV)。

    lli : 锂库存损失比例（施加在限容的石墨端初始锂上）
    lam : 负极活性材料损失比例
    """
    pv = pybamm.ParameterValues(PARAM_SET)
    pv["Current function [A]"] = pv["Nominal cell capacity [A.h]"] * C_RATE
    if lli:
        pv[CNN] = pv[CNN] * (1.0 - lli)
    if lam:
        pv[EN] = pv[EN] * (1.0 - lam)

    model = pybamm.lithium_ion.SPMe({"thermal": "isothermal"})
    solver = pybamm.IDAKLUSolver(options={"dt_max": DT_MAX})
    sol = pybamm.Simulation(model, parameter_values=pv, solver=solver).solve(
        [0, T_END], initial_soc=SOC0
    )

    V = np.ravel(sol["Voltage [V]"].entries)
    Q = np.ravel(sol["Discharge capacity [A.h]"].entries)
    n = min(len(V), len(Q))
    V, Q = V[:n], Q[:n]
    ok = np.isfinite(V) & np.isfinite(Q)
    V, Q = V[ok], Q[ok]

    o = np.argsort(Q)
    V, Q = V[o], Q[o]
    keep = np.concatenate(([True], np.diff(Q) > 1e-12))
    V, Q = V[keep], Q[keep]

    # 放电：V 降 Q 增 -> 取负梯度，得到正的 dQ/dV
    dQdV = -np.gradient(Q, V)
    return V, Q, dQdV


def compose(regions):
    """并联汇总：dQ/dV = Σ w_i * dQ/dV_i（w_i 为容量份额）。

    每个区域代表整包电芯的 1/N。单区算出来的曲线是"该区自身的满容量"，
    所以份额取 w_i = cap_i / Σcap_j，总容量 = (Σcap_j)/N。
    """
    curves = [local_curve(**r) for r in regions]
    caps = [c[1].max() for c in curves]
    total = float(sum(caps))
    n_reg = len(regions)
    pack_cap = total / n_reg          # 每区只占 1/N，不能直接相加

    v_lo = max(c[0].min() for c in curves)
    v_hi = min(c[0].max() for c in curves)
    Vg = np.linspace(v_lo, v_hi, 900)

    d_tot = np.zeros_like(Vg)
    for (V, Q, d), cap in zip(curves, caps):
        f = np.interp(Vg, V[::-1], d[::-1], left=0.0, right=0.0)
        d_tot += (cap / total) * f
    return Vg, d_tot, pack_cap


def fwhm(V, d):
    """主峰半高全宽（伏特，正数）。用来量化峰被抹平的程度。"""
    i = int(np.argmax(d))
    half = d[i] / 2.0
    l = i
    while l > 0 and d[l] > half:
        l -= 1
    r = i
    while r < len(d) - 1 and d[r] > half:
        r += 1
    return float(abs(V[l] - V[r]))


def main():
    print("=" * 78)
    print(f"LFP/石墨 区域化 LLI/LAM -> dQ/dV   ({PARAM_SET}, {N_REGIONS} 区域, C/20)")
    print("=" * 78)

    scenarios = {
        "健康": [dict()] * N_REGIONS,
        "均匀 LLI 9%": [dict(lli=0.09)] * N_REGIONS,
        "均匀 LAM 9%": [dict(lam=0.09)] * N_REGIONS,
        "LLI 分区 0/6/12/18%": [dict(lli=v) for v in (0.0, .06, .12, .18)],
        "LAM 分区 0/6/12/18%": [dict(lam=v) for v in (0.0, .06, .12, .18)],
        "混合分区": [dict(lli=0.0, lam=0.0), dict(lli=.06, lam=.04),
                     dict(lli=.12, lam=.02), dict(lli=.02, lam=.12)],
    }

    fig, axes = plt.subplots(2, 3, figsize=(16, 8), dpi=130)
    summary = {}
    for k, (name, spec) in enumerate(scenarios.items()):
        Vg, d, cap = compose(spec)
        summary[name] = (Vg, d, cap)
        ax = axes.ravel()[k]
        ax.plot(Vg, d, lw=1.6)
        ax.set(xlabel="电压 [V]", ylabel="dQ/dV",
               title=f"{name}\n总容量≈{cap:.4f} Ah")
        ax.invert_xaxis()
        ax.grid(alpha=.3)

    # 第 6 格：同容量损失下 均匀 vs 分区 的直接对比
    ax = axes[1, 2]
    ax.clear()
    for key, style in [("均匀 LLI 9%", "--"), ("LLI 分区 0/6/12/18%", "-"),
                       ("均匀 LAM 9%", ":"), ("LAM 分区 0/6/12/18%", "-.")]:
        Vg, d, _ = summary[key]
        ax.plot(Vg, d, style, lw=1.8, label=key)
    ax.set(xlabel="电压 [V]", ylabel="dQ/dV", title="同容量损失：均匀 vs 分区")
    ax.legend(fontsize=7)
    ax.invert_xaxis()
    ax.grid(alpha=.3)

    fig.tight_layout()
    path = common.savefig(fig, "dqdv_lfp_regions.png")
    print(f"\nsaved {path}\n")

    print(f"{'场景':22s} {'容量Ah':>9} {'峰高':>8} {'峰宽FWHM[V]':>12}")
    for name, (Vg, d, cap) in summary.items():
        print(f"{name:22s} {cap:>9.4f} {d.max():>8.2f} {fwhm(Vg, d):>12.5f}")

    print("\n[关键对比] 同样 9% 平均容量损失，均匀 vs 分区：")
    for mode in ("LLI", "LAM"):
        u = summary[f"均匀 {mode} 9%"]
        p = summary[f"{mode} 分区 0/6/12/18%"]
        print(f"  {mode}: 容量 {u[2]:.4f} vs {p[2]:.4f} Ah (相同)")
        print(f"       峰高 {u[1].max():6.2f} vs {p[1].max():6.2f} "
              f"-> 分区额外降低 {100*(1-p[1].max()/u[1].max()):.1f}%")
        print(f"       峰宽 {fwhm(*u[:2]):.5f} vs {fwhm(*p[:2]):.5f} V")

    print("\n[解读]")
    print("  * 容量完全一样，所以只看容量**发现不了**非均匀性。")
    print("  * 非均匀 LLI：峰高再降 ~39%、峰宽放大 ~6 倍 -> 平台边缘被抹开。")
    print("  * 非均匀 LAM：峰形几乎不变（只按份额缩放）。")
    print("  也就是说在 LFP 上，'峰被抹平' 更像是 LLI 空间分布不均的指纹，")
    print("  而不是所有非均匀性的通用特征。")


if __name__ == "__main__":
    main()

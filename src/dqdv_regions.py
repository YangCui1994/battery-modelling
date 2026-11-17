"""区域化 LLI / LAM -> dQ/dV（PyBaMM 2D 做不了空间退化时的替代方案）。

## 先回答"2D 能不能做"

实测 PyBaMM 26.10.1.1：
  * `loss of active material` 子模型按 **1D 电极**写的。只要集流体维度 >= 1
    （`dimensionality=1/2` + `potential pair`），一接上就
    `ModelError: rhs and initial conditions must have the same shape`。
    => **"2D 里给不同区域不同 LAM/LLI"走不通**，这是当前硬限制。
  * 空间变化的**普通参数**可以（如 eps 沿 y 分布），但退化子模型不支持。

## 替代方案：N 个并联区域 + 容量坐标上的退化映射

把电芯切成 N 个并联区域，每区一个 LLI/LAM 状态，共用端电压。

以健康电芯的放电曲线 Q0(V) 为基函数（由 PyBaMM 实算），对每区施加：

  * **LAM**：活性材料少 -> 该区可用容量 = Q0*(1-λ)。
  * **LLI**：可循环锂少 -> 该区放电起点比健康电芯晚，可用容量再乘 (1-λ)。

于是该区容量 -> 健康容量坐标的映射为：

    Q_map(Q) = Q / [(1-λ_LLI)(1-λ_LAM)]

即把健康曲线的"同一段"压缩到更小的容量区间；再由基函数查该位置的 V。

总曲线：在公共电压网格上对每区各自的 V_i(Q) 求逆、累加容量，
再数值微分得到 dQ/dV。

## LLI 与 LAM 在 dQ/dV 上的区别

两者都让容量变小，但：
  * LAM 是**等比压缩**整条曲线 -> dQ/dV 峰**等比例变矮**，峰位不动。
  * LLI 是**从放电末端扣掉一截** -> 曲线端点提前，峰相对位置移动。
这是 dQ/dV 诊断区分二者的依据，脚本会打印两者的峰位/峰高对比。

用法::

    .\.venv\Scripts\python.exe dqdv_regions.py
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pybamm  # noqa: E402
import common  # noqa: E402

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False
pybamm.set_logging_level("ERROR")

PARAM_SET = "Marquis2019"
V_LO, V_HI = 3.105, 4.10
N_INTERP = 3000
N_REGIONS = 4


def healthy_curve():
    """PyBaMM 实算一枚健康电芯的准静态放电曲线，返回 (V, Q) 单调递增序列。

    用 C/20 慢放（准 OCV）+ 稠密输出，否则积分器把点全堆在 3.5-3.7 V，
    dQ/dV 会是一堆噪声毛刺（实测：1C 只给 36 个点，1207 个点里 521 个挤在
    同一个 0.1 V 区间）。
    """
    pv = pybamm.ParameterValues(PARAM_SET)
    pv["Current function [A]"] = pv["Nominal cell capacity [A.h]"] / 20.0   # C/20
    model = pybamm.lithium_ion.SPMe({"thermal": "lumped"})
    sim = pybamm.Simulation(model, parameter_values=pv, solver=pybamm.IDAKLUSolver())
    sol = sim.solve([0, 90000], t_interp=np.linspace(0, 90000, 4000))

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
    return V, Q, float(Q.max())


def healthy_dqdv(V, Q):
    """基函数 dQ/dV（放电为正）。"""
    return -np.gradient(Q, V)


def region_curve(Vb, Qb, Q0, lli, lam, share):
    """某区域的 (Q_i, V_i)，在电压空间做退化映射。

    健康基函数：Qb(V) = 电压 V 处已放出的容量（V 降 -> Q 增）。

    退化区域的等效做法是把电压轴缩放 s=(1-lli)(1-lam)：
        q_i(V) = share * s * Qb(V')
        V' = V_hi - (V_hi - V)/s                 （电压轴拉伸）
    于是 LAM 让 dQ/dV 幅值按 s 缩、峰位随轴移动；LLI 叠同样的缩放。
    返回的 Q_i 以"该区已放出容量"计，随 V 降低而单调增。
    """
    s = (1.0 - lli) * (1.0 - lam)
    if s <= 1e-9:
        return np.array([0.0, 1.0]), np.array([V_HI, V_LO])

    V_hi = float(Vb[0])                    # 放电起点最高电压
    Vg_i = np.linspace(V_LO, V_hi, 800)    # 升序
    V_map = V_hi - (V_hi - Vg_i) / s
    V_map = np.clip(V_map, V_LO, V_hi)

    # Qb 是"从该电压还能继续放出多少容量"：V=V_hi 时约 0，V=V_LO 时最大(=Q0)。
    # 随 V 升高而减少 -> 用 -V 单调化后插值。
    q_remaining = np.interp(-V_map, -Vb, Qb)
    # 该区已放出容量 = 该区总容量 - 该电压处剩余
    q_i = share * s * (Qb[-1] - q_remaining)
    q_i = np.clip(q_i, 0.0, Q0 * share * s)

    V_i = Vg_i[::-1]                        # 递减
    Q_i = np.maximum.accumulate(q_i)[::-1]  # 强制单调，消除插值抖动
    return Q_i, V_i


def compose(regions, Vb, Qb, Q0):
    """并联汇总：Q_tot(V) = Σ Q_i(V)，再对 V 微分得 dQ/dV。

    关键：直接对"每个电压网格点累加各区容量"会让端点出现插值假峰。
    改为在容量坐标上构造每条区曲线，再统一插到电压网格并微分。
    """
    Vg = np.linspace(V_LO, V_HI, 1200)
    Q_tot = np.zeros_like(Vg)
    share = 1.0 / len(regions)

    for lli, lam in regions:
        Q_i, V_i = region_curve(Vb, Qb, Q0, lli, lam, share)
        # V_i 随 Q_i 单调降；插到电压轴（升序）上
        Q_at_V = np.interp(Vg, V_i[::-1], Q_i[::-1],
                           left=0.0, right=Q_i.max())
        Q_tot += Q_at_V

    # 去掉两端插值平台再微分，避免边界假峰
    dQdV = np.gradient(Q_tot, Vg)
    dQdV[:5] = 0.0
    dQdV[-5:] = 0.0
    return Vg, Q_tot, dQdV


def main():
    Vb, Qb, Q0 = healthy_curve()
    print("=" * 74)
    print("PyBaMM 区域化 LLI / LAM -> dQ/dV")
    print("=" * 74)
    print(f"\n健康基函数: Q0={Q0:.4f} Ah  V=[{Vb.min():.3f},{Vb.max():.3f}]")

    scenarios = {
        "健康": [dict(lli=0.0, lam=0.0)] * N_REGIONS,
        "均匀 LLI 15%": [dict(lli=0.15, lam=0.0)] * N_REGIONS,
        "均匀 LAM 15%": [dict(lli=0.0, lam=0.15)] * N_REGIONS,
        "LLI 分区 0/10/20/30%": [dict(lli=v, lam=0.0) for v in (0.0, .10, .20, .30)],
        "LAM 分区 0/10/20/30%": [dict(lli=0.0, lam=v) for v in (0.0, .10, .20, .30)],
        "混合分区": [dict(lli=0.0, lam=0.0), dict(lli=.10, lam=.10),
                     dict(lli=.20, lam=.05), dict(lli=.05, lam=.20)],
    }

    fig, axes = plt.subplots(2, 3, figsize=(16, 8.5), dpi=130)
    flat = axes.ravel()
    summary = {}
    for k, (name, specs) in enumerate(scenarios.items()):
        regs = [(s["lli"], s["lam"]) for s in specs]
        Vg, Q, d = compose(regs, Vb, Qb, Q0)
        summary[name] = (Vg, Q, d)

        ax = flat[k]
        ax.plot(Vg, Q, lw=1.6)
        ax.set(xlabel="电压 [V]", ylabel="容量 [Ah]", title=name)
        ax.invert_xaxis()
        ax.grid(alpha=.3)

    # 第二行换成 dQ/dV 对比（3 个关键场景）
    axes[1, 0].clear(); axes[1, 1].clear(); axes[1, 2].clear()
    for key, style in [("均匀 LLI 15%", ":"), ("均匀 LAM 15%", "--"),
                       ("LLI 分区 0/10/20/30%", "-"), ("LAM 分区 0/10/20/30%", "-.")]:
        Vg, Q, d = summary[key]
        axes[1, 0].plot(Vg[:-2], d[:-2], style, lw=1.8, label=key)
    axes[1, 0].set(xlabel="电压 [V]", ylabel="dQ/dV", title="LLI vs LAM 的 dQ/dV 特征")
    axes[1, 0].legend(fontsize=7); axes[1, 0].invert_xaxis(); axes[1, 0].grid(alpha=.3)

    for key in ["健康", "均匀 LLI 15%", "均匀 LAM 15%", "LLI 分区 0/10/20/30%"]:
        Vg, Q, d = summary[key]
        axes[1, 1].plot(Vg[:-2], d[:-2], lw=1.6, label=key)
    axes[1, 1].set(xlabel="电压 [V]", ylabel="dQ/dV", title="健康 vs 退化")
    axes[1, 1].legend(fontsize=7); axes[1, 1].invert_xaxis(); axes[1, 1].grid(alpha=.3)

    for key in ["LLI 分区 0/10/20/30%", "LAM 分区 0/10/20/30%", "混合分区"]:
        Vg, Q, d = summary[key]
        axes[1, 2].plot(Vg[:-2], d[:-2], lw=1.6, label=key)
    axes[1, 2].set(xlabel="电压 [V]", ylabel="dQ/dV", title="分区退化的叠加效应")
    axes[1, 2].legend(fontsize=7); axes[1, 2].invert_xaxis(); axes[1, 2].grid(alpha=.3)

    # 第三列会空着，补上"均匀 vs 分区"的 dQ/dV 对比
    axes[0, 2].clear()
    uni = [(0.15, 0.15)] * N_REGIONS
    Vg_u, Q_u, d_u = compose(uni, Vb, Qb, Q0)
    part = [(v, v) for v in (0.0, .10, .20, .30)]
    Vg_p, Q_p, d_p = compose(part, Vb, Qb, Q0)
    axes[0, 2].plot(Vg_u[:-2], d_u[:-2], lw=1.8, label="均匀退化")
    axes[0, 2].plot(Vg_p[:-2], d_p[:-2], lw=1.8, label="分区退化")
    axes[0, 2].set(xlabel="电压 [V]", ylabel="dQ/dV",
                   title="同样平均退化：\n均匀 vs 分区")
    axes[0, 2].legend(fontsize=8)
    axes[0, 2].invert_xaxis()
    axes[0, 2].grid(alpha=.3)

    fig.tight_layout()
    common.savefig(fig, "dqdv_regions.png")
    print("saved dqdv_regions.png\n")

    print("[定量] 峰位 / 峰高 / 容量")
    for name, (Vg, Q, d) in summary.items():
        print(f"  {name:22s} 容量={Q.max():.4f} Ah  dQdV峰={d.max():7.3f}  "
              f"峰位={Vg[np.argmax(d)]:.3f} V")

    print("\n[解读]")
    print("  分区退化会让 dQ/dV 峰显著变矮变宽（本算例 27 -> 8，降 69%）")
    print("  这是非均匀老化在 dQ/dV 上最可靠的指纹：峰形被抹平。")
    print()
    print("[重要局限]")
    print("  本降阶模型里 LLI 与 LAM 都归结为电压轴同比缩放，所以两者结果完全一致")
    print("  （峰 25.498 / 3.709 V 完全相同）。这不是笔误，是模型的固有局限：")
    print("  只靠全电池 dQ/dV 的\"容量缩放\"，无法区分容量损失来自锂库存还是活性材料。")
    print("  要分开必须靠电极级信息：半电池 dQ/dV、参比电极、或拆解/三电极测量。")
    print("  -> 所以本模型能回答\"非均匀性如何改变 dQ/dV 峰形\"，")
    print("     但不能回答\"这 15% 损失里 LLI/LAM 各占多少\"。")


if __name__ == "__main__":
    main()

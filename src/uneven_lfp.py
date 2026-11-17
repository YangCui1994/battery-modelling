"""LFP/石墨 电芯：多种空间不均匀性模式 -> dQ/dV 对比（2.5-3.65 V 充放）。

## 为什么要"多种模式"

同一个平均退化量，空间分布不同，dQ/dV 会不同。本脚本固定**平均退化量相同**，
只改变空间的**形状**，从而单独看"形状"的影响：

  * 均匀      ：所有区域一样
  * 线性梯度  ：沿一个方向单调变化
  * 局部热点  ：只有一个区域严重退化
  * 随机散布  ：几个区域高低交错

## 模型

沿用区域并联（见 `dqdv_lfp.py`）：N 个区域各自独立 LLI/LAM，共用端电压，
总 dQ/dV = Σ w_i · dQ/dV_i，w_i 为容量份额。

PyBaMM 2D 不能直接做空间退化（LAM 子模型接集流体维度就 ModelError），
所以这是目前唯一能跑通的路子。

## 该电芯的退化杠杆（实测）

Prada2013 是**石墨限容**：
  * LAM -> `Negative electrode active material volume fraction`
  * LLI -> `Initial concentration in negative electrode`

用法::

    python src/uneven_lfp.py
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pybamm  # noqa: E402

import common  # noqa: E402

common.setup_matplotlib()
pybamm.set_logging_level("ERROR")

PARAM_SET = "Prada2013"
V_BOTTOM, V_TOP = 2.5, 3.65
C_RATE = 1.0
SOC_START = 0.01555
DT_MAX = 0.5
N_REG = 5

CNN = "Initial concentration in negative electrode [mol.m-3]"
EN = "Negative electrode active material volume fraction"


# ---------------------------------------------------------------- 单区域充/放
def region_curve(lli: float, lam: float, charge: bool):
    """跑单个区域的一次充电或放电，返回 (V, Q, dQ/dV)。"""
    pv = pybamm.ParameterValues(PARAM_SET)
    cap = pv["Nominal cell capacity [A.h]"]
    pv["Current function [A]"] = -cap * C_RATE if charge else cap * C_RATE
    pv["Upper voltage cut-off [V]"] = V_TOP
    pv["Lower voltage cut-off [V]"] = V_BOTTOM
    if lli:
        pv[CNN] = pv[CNN] * (1.0 - lli)
    if lam:
        pv[EN] = pv[EN] * (1.0 - lam)

    model = pybamm.lithium_ion.SPMe({"thermal": "isothermal"})
    solver = pybamm.IDAKLUSolver(options={"dt_max": DT_MAX})
    # 用 Experiment：直接 solve 会因起始电压触及事件而报
    # "Events minimum voltage are non-positive at initial conditions"
    step = (f"Charge at {C_RATE}C until {V_TOP} V" if charge
            else f"Discharge at {C_RATE}C until {V_BOTTOM} V")
    sim = pybamm.Simulation(model, experiment=pybamm.Experiment([step]),
                            parameter_values=pv, solver=solver)
    sol = sim.solve(initial_soc=SOC_START if charge else 1.0)
    sub = sol.cycles[0]
    sub = sub.sub_solutions[0] if hasattr(sub, "sub_solutions") else sub

    V = np.ravel(sub["Voltage [V]"].entries)
    Q = np.ravel(sub["Throughput capacity [A.h]"].entries)
    n = min(len(V), len(Q))
    V, Q = V[:n], Q[:n]
    ok = np.isfinite(V) & np.isfinite(Q)
    V, Q = V[ok], Q[ok]
    Q = Q - Q[0]
    d = dqdv_robust(V, Q)
    return V, Q, d


def dqdv_robust(V, Q, dv=1e-7, spike=20.0):
    """稳健 |dQ/dV|：过滤重复电压点与个别容量跳变（见 cycle_lfp.py）。"""
    dV, dQ = np.diff(V), np.diff(Q)
    Vm = 0.5 * (V[1:] + V[:-1])
    ok = np.abs(dV) >= dv
    rate = np.abs(dQ[ok] / dV[ok])
    med = float(np.median(rate)) if rate.size else 0.0
    keep = rate <= spike * med if med > 0 else np.ones_like(rate, bool)
    idx = np.where(ok)[0][keep]
    o = np.argsort(Vm[idx])
    return Vm[idx][o], np.abs(dQ[idx] / dV[idx])[o]


def compose(specs, charge: bool):
    """把 N 个区域并联汇总。返回 (V, dQ/dV, 总容量, 各区容量)。

    权重用**绝对容量份额** w_i = cap_i / (N * cap_healthy_ref)，
    而不是 cap_i / Σcap_j。

    为什么不能用归一化权重：归一化会把总容量信息抹掉，导致"热点"模式
    （4 个健康区 + 1 个很小区域）反而让健康峰占更大比重，峰高比均匀还高，
    模式之间就不可比了。用绝对份额后，总面积正比于真实总容量，
    峰高才是可比的。
    """
    curves = [region_curve(s["lli"], s["lam"], charge) for s in specs]
    caps = [float(c[1].max()) for c in curves]
    # 同时记录各区域的峰位/峰高：这是判断"峰是否真的分离"的依据
    reg_peaks = []
    for (V_raw, Q_raw, (Vg_i, d_i)) in curves:
        m = (Vg_i >= 2.6) & (Vg_i <= 3.6)
        if m.any():
            reg_peaks.append({"peak": float(d_i[m].max()),
                              "peak_v": float(Vg_i[m][np.argmax(d_i[m])])})
        else:
            reg_peaks.append({"peak": 0.0, "peak_v": float("nan")})
    total = float(sum(caps))

    # 健康单区容量（所有区域都不退化时的容量）作为归一化基准
    ref = float(region_curve(0.0, 0.0, charge)[1].max())
    n = len(specs)

    v_lo = max(c[0].min() for c in curves)
    v_hi = min(c[0].max() for c in curves)
    Vg = np.linspace(v_lo, v_hi, 1200)

    d_tot = np.zeros_like(Vg)
    # region_curve 返回 (V, Q, (Vg, dQdV))，第三项本身是二元组
    for (V_raw, Q_raw, (Vg_i, d_i)), cap in zip(curves, caps):
        f = np.interp(Vg, Vg_i, d_i, left=0.0, right=0.0)
        d_tot += (cap / (n * ref)) * f
    return Vg, d_tot, total / n, caps, reg_peaks


def peak_stats(V, d, lo=2.6, hi=3.6):
    m = (V >= lo) & (V <= hi)
    if not m.any():
        return np.nan, np.nan, np.nan
    Vm, dm = V[m], d[m]
    i = int(np.argmax(dm))
    half = dm[i] / 2.0
    l = i
    while l > 0 and dm[l] > half:
        l -= 1
    r = i
    while r < len(dm) - 1 and dm[r] > half:
        r += 1
    return float(dm[i]), float(Vm[i]), float(abs(Vm[l] - Vm[r]))


def main():
    print("=" * 78)
    print(f"LFP/石墨 不均匀性模式 -> dQ/dV   ({PARAM_SET}, {N_REG} 区域, 1C)")
    print("=" * 78)

    # 平均退化量固定为 10%（LLI 与 LAM 各占一半），只改空间形状
    mean = 0.10
    half = mean / 2.0

    def mk(lli_list, lam_list):
        return [{"lli": a, "lam": b} for a, b in zip(lli_list, lam_list)]

    # 所有模式的平均退化量都严格等于 mean（LLI/LAM 各 mean/2）
    # N_REG=5 时：
    #   均匀     : 全为 mean/2
    #   线性梯度 : 0, m/4, m/2, 3m/4, m  -> 均值 m
    #   局部热点 : 0,0,0,0, 5m -> 均值 m
    #   随机散布 : 0, 2.5m/2, 0, 2.5m/2, m/2 -> 均值 m/2 ... 见下方显式构造
    full = mean / 2.0                       # 单通道（LLI 或 LAM）的平均值
    patterns = {
        "均匀": mk([full] * N_REG, [full] * N_REG),
        "线性梯度": mk(np.linspace(0, 2 * full, N_REG),
                       np.linspace(0, 2 * full, N_REG)),
        "局部热点": mk([0.0] * 4 + [full * N_REG],
                       [0.0] * 4 + [full * N_REG]),
        # 随机散布：两区偏高、两区为零、一区中等，均值仍等于 full
        # 0 + 0 + 0 + 1.25f + 3.75f = 5f -> 均值 f
        "随机散布": mk([0.0, 0.0, 0.0, full * 1.25, full * 3.75],
                       [0.0, 0.0, 0.0, full * 1.25, full * 3.75]),
    }
    # 校验平均量一致
    for k, s in patterns.items():
        ml = np.mean([x["lli"] for x in s])
        ma = np.mean([x["lam"] for x in s])
        print(f"  {k:8s} 平均 LLI={ml:.3f} LAM={ma:.3f}")

    results = {}
    for name, specs in patterns.items():
        res = {}
        for charge in (True, False):
            tag = "充电" if charge else "放电"
            Vg, d, cap, caps, reg_peaks = compose(specs, charge)
            popt, vpos, fw = peak_stats(Vg, d)
            # 理想（无展宽）峰高 = 各区峰高按容量加权
            res[tag] = {"cap": cap, "peak": popt, "peak_v": vpos,
                        "fwhm": fw, "region_caps": caps,
                        "region_peaks": reg_peaks,
                        "peak_span_mv": (max(r["peak_v"] for r in reg_peaks)
                                         - min(r["peak_v"] for r in reg_peaks)) * 1000}
        res["容量差"] = res["充电"]["cap"] - res["放电"]["cap"]
        results[name] = res
        print(f"\n[{name}]")
        for tag in ("充电", "放电"):
            r = res[tag]
            print(f"  {tag}: 容量={r['cap']:.4f} Ah  dQdV峰={r['peak']:.1f} "
                  f"@ {r['peak_v']:.3f} V  FWHM={r['fwhm']*1000:.1f} mV  "
                  f"各区峰位跨度={r['peak_span_mv']:.1f} mV")

    # ---------- 图 ----------
    fig, axes = plt.subplots(2, 3, figsize=(16, 8.5), dpi=130)
    colors = plt.cm.tab10(np.linspace(0, 1, 10))

    ax = axes[0, 0]
    for i, (name, specs) in enumerate(patterns.items()):
        ax.plot([s["lli"] * 100 for s in specs], "o-", color=colors[i], label=name)
    ax.set(xlabel="区域编号", ylabel="LLI [%]", title="各模式的空间分布")
    ax.legend(fontsize=8)
    ax.grid(alpha=.3)

    ax = axes[0, 1]
    for i, (name, specs) in enumerate(patterns.items()):
        Vg, d, _, _, _ = compose(specs, charge=False)
        ax.plot(Vg, d, color=colors[i], lw=1.7, label=name)
    ax.set(xlabel="电压 [V]", ylabel="dQ/dV", title="放电 dQ/dV（平均退化量相同）")
    ax.legend(fontsize=8)
    ax.grid(alpha=.3)

    ax = axes[0, 2]
    for i, (name, specs) in enumerate(patterns.items()):
        Vg, d, _, _, _ = compose(specs, charge=True)
        ax.plot(Vg, d, color=colors[i], lw=1.7, label=name)
    ax.set(xlabel="电压 [V]", ylabel="dQ/dV", title="充电 dQ/dV")
    ax.legend(fontsize=8)
    ax.grid(alpha=.3)

    ax = axes[1, 0]
    names = list(patterns)
    ax.bar(names, [results[n]["放电"]["peak"] for n in names], color="tab:blue")
    ax.set(ylabel="放电 dQ/dV 峰高", title="峰高对比（越低=抹平越多）")
    ax.tick_params(axis="x", rotation=15)
    ax.grid(alpha=.3, axis="y")

    ax = axes[1, 1]
    ax.bar(names, [results[n]["放电"]["fwhm"] * 1000 for n in names], color="tab:orange")
    ax.set(ylabel="FWHM [mV]", title="峰宽对比（越宽=平台边缘越分散）")
    ax.tick_params(axis="x", rotation=15)
    ax.grid(alpha=.3, axis="y")

    ax = axes[1, 2]
    ax.bar(names, [results[n]["放电"]["cap"] for n in names], color="tab:green")
    ax.set(ylabel="放电容量 [Ah]", title="容量对比")
    ax.tick_params(axis="x", rotation=15)
    ax.set_ylim(0.9, max(results[n]["放电"]["cap"] for n in names) * 1.02)
    ax.grid(alpha=.3, axis="y")

    fig.tight_layout()
    path = common.savefig(fig, "uneven_lfp_patterns.png")
    print(f"\nsaved {path}")

    out = Path(common.results_dir()) / "uneven_lfp_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {out}")


if __name__ == "__main__":
    main()

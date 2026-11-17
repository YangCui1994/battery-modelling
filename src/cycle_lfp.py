"""LFP/石墨 1C 完整循环：2.5 V -> 3.65 V -> 2.5 V。

一次充放循环能给出的三个量：

  * **库仑效率 CE** = 放电容量 / 充电容量
  * **能量效率 EE** = 放电能量 / 充电能量。比 CE 低得多，因为充放间存在
    约 100 mV 电压迟滞，每循环都有可观能量损失。
  * **dQ/dV 迟滞**：充、放 dQ/dV 画在同一电压轴上，峰位差即迟滞电压。

## 三个容易算错的坑（都实测踩过）

1. **电流符号**：正=放电，负=充电。
2. **`Throughput capacity [A.h]` 是全实验累计值，不是本步值。**
   必须用相邻 step 的**差分** ΔQ。否则会把 2.24 + 1.86 当成放电容量 4.10 Ah。
   静置步 ΔQ≈0，可直接过滤。
3. **第一个循环的充电容量偏高（2.2448 Ah），不能拿来做 CE。**
   因为起点 SOC 是 1.56%（为满足"从 2.5 V 充电"），首充要补上这段。
   从第二循环起稳态值为 1.8559 Ah，充放完全对称 -> CE≈100%。
   所以 CE 必须取**稳态循环**，不能用第 1 个循环。

用法::

    python src/cycle_lfp.py [循环数]
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
V_BOTTOM, V_TOP = 2.5, 3.65
C_RATE = 1.0
SOC_START = 0.01555          # 1C 充电下端电压恰为 2.5 V
DT_MAX = 0.5
N_CYCLES = int(sys.argv[1]) if len(sys.argv) > 1 else 3


def run_cycles():
    pv = pybamm.ParameterValues(PARAM_SET)
    pv["Current function [A]"] = pv["Nominal cell capacity [A.h]"] * C_RATE
    pv["Upper voltage cut-off [V]"] = V_TOP
    pv["Lower voltage cut-off [V]"] = V_BOTTOM

    model = pybamm.lithium_ion.SPMe({"thermal": "isothermal"})
    experiment = pybamm.Experiment([
        f"Charge at {C_RATE}C until {V_TOP} V",
        "Rest for 10 minutes",
        f"Discharge at {C_RATE}C until {V_BOTTOM} V",
        "Rest for 10 minutes",
    ] * N_CYCLES)
    sim = pybamm.Simulation(
        model, experiment=experiment, parameter_values=pv,
        solver=pybamm.IDAKLUSolver(options={"dt_max": DT_MAX}),
    )
    return sim.solve(initial_soc=SOC_START)


def steps(sol):
    """把每个 step 拆成 (V, Q_本步, ΔQ, 终止事件)。Q 从 0 起算是本步内容量。"""
    out, prev = [], 0.0
    for c in sol.cycles:
        s = c.sub_solutions[0] if hasattr(c, "sub_solutions") else c
        Qcum = np.ravel(s["Throughput capacity [A.h]"].entries)
        dQ = float(Qcum[-1] - prev)
        prev = Qcum[-1]
        # 本步内已转移容量（从 0 开始），用于画曲线
        Qin = Qcum - Qcum[0]
        out.append({
            "V": np.ravel(s["Voltage [V]"].entries),
            "Q": Qin,
            "dQ": dQ,
            "term": str(getattr(c, "termination", "")),
        })
    return out


def energy(V, Q):
    """∫V dQ，Q 单位 Ah -> Wh。"""
    return float(np.trapezoid(V, Q))


def dqdv(V, Q, dv=1e-7, spike_factor=20.0):
    """求 |dQ/dV|，对求解器的重复电压/容量跳变做稳健处理。

    直接在原始序列上做相邻差分最稳（Q 随时间单调增，V 除个别点外单调降）。

    两组实测出来的过滤阈值：
    * 丢弃 |dV| < 1e-7：重复电压点会让分母趋零。实测典型 ΔV 约 5e-5，
      取 1e-7 不会误伤正常点（曾误用 1e-3，结果只剩 159 个点）。
    * 丢弃 dQ/dV > 20x 中位数：求解器在 3.24 V 附近有一次容量跳变
      （3.240139->3.240138 V 之间容量跳 0.013 Ah），会伪造 1e3 量级尖峰。
      实测中位数约 6，正常峰约 122，20x 阈值能把两者分开。

    返回按电压升序排序的 (V, dQ/dV)。
    """
    V = np.asarray(V, float)
    Q = np.asarray(Q, float)
    dV = np.diff(V)
    dQ = np.diff(Q)
    Vmid = 0.5 * (V[1:] + V[:-1])
    ok = np.abs(dV) >= dv

    rate = np.abs(dQ[ok] / dV[ok])
    med = float(np.median(rate)) if rate.size else 0.0
    keep = rate <= spike_factor * med if med > 0 else np.ones_like(rate, bool)

    idx = np.where(ok)[0][keep]
    Vg = Vmid[idx]
    d = np.abs(dQ[idx] / dV[idx])
    o = np.argsort(Vg)
    return Vg[o], d[o]


def main():
    print("=" * 76)
    print(f"LFP 1C 循环 {V_BOTTOM}->{V_TOP}->{V_BOTTOM} V  x{N_CYCLES}   ({PARAM_SET})")
    print("=" * 76)

    sol = run_cycles()
    st = steps(sol)

    print(f"\nstep 数={len(st)}（每循环 4 步：充/静置/放/静置）")
    print(f"{'step':>4} {'ΔQ[Ah]':>9} {'V_end':>8}  终止")
    for i, d in enumerate(st):
        print(f"{i:>4} {d['dQ']:>9.4f} {d['V'][-1]:>8.3f}  {d['term'][:40]}")

    # 按充/放配对（充电步有 "Voltage >", 放电步有 "Voltage <"）
    charges = [d for d in st if ">" in d["term"]]
    dischs = [d for d in st if "<" in d["term"]]
    print(f"\n充电步 {len(charges)} 个，放电步 {len(dischs)} 个")

    # 稳态循环（跳过第一个充电步，它含初始补齐）
    c_caps = [d["dQ"] for d in charges[1:]]
    d_caps = [d["dQ"] for d in dischs[1:]]
    print("\n[循环性能]（稳态，跳过首循环）")
    print(f"  充电容量  {np.mean(c_caps):.4f} Ah  (各循环 {['%.4f' % v for v in c_caps]})")
    print(f"  放电容量  {np.mean(d_caps):.4f} Ah  (各循环 {['%.4f' % v for v in d_caps]})")
    if c_caps and d_caps:
        print(f"  库仑效率 CE = {np.mean(d_caps) / np.mean(c_caps) * 100:.3f} %")

    # 能量效率（用稳态那对曲线）
    ch = charges[-1] if len(charges) > 1 else charges[0]
    dis = dischs[-1]
    e_ch, e_dis = energy(ch["V"], ch["Q"]), energy(dis["V"], dis["Q"])
    print(f"\n  充电能量 {e_ch:.4f} Wh   放电能量 {e_dis:.4f} Wh")
    print(f"  能量效率 EE = {e_dis / e_ch * 100:.3f} %")
    print(f"  每循环能量损失 = {(e_ch - e_dis) * 1000:.1f} mWh")

    # ---- 图 ----
    Vc, dc = dqdv(ch["V"], ch["Q"])
    Vd, dd = dqdv(dis["V"], dis["Q"])

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.3), dpi=130)

    # 充放曲线用原始 V/Q（不用过滤后的点，避免碎线）
    axes[0].plot(ch["Q"], ch["V"], lw=1.9, label="充电")
    axes[0].plot(dis["Q"], dis["V"], lw=1.9, label="放电")
    axes[0].set(xlabel="容量 [Ah]", ylabel="电压 [V]", title="1C 充放曲线（稳态循环）")
    axes[0].legend()
    axes[0].grid(alpha=.3)

    axes[1].plot(Vc, dc, lw=1.9, label="充电 dQ/dV")
    axes[1].plot(Vd, dd, lw=1.9, label="放电 dQ/dV")
    axes[1].set(xlabel="电压 [V]", ylabel="dQ/dV", title="dQ/dV 迟滞")
    axes[1].legend()
    axes[1].grid(alpha=.3)

    # 迟滞电压：对两条原始曲线按容量插值
    # 注意：两条曲线的可用容量端点不同（充放起止 SOC 不同），端点处 ΔV 会发散
    # （实测尾部能到 1100 mV），所以统计量取中间 80% 区间，端点只画不统计。
    n = 400
    lo = max(float(ch["Q"].min()), float(dis["Q"].min()))
    hi = min(float(ch["Q"].max()), float(dis["Q"].max()))
    qg = np.linspace(lo, hi, n)
    vch = np.interp(qg, ch["Q"], ch["V"])
    vdis = np.interp(qg, dis["Q"], dis["V"])
    dv = vch - vdis
    mid = slice(int(0.1 * n), int(0.9 * n))
    dv_mid = dv[mid]

    axes[2].plot(qg, dv * 1000, lw=1.9)
    axes[2].fill_between(qg, 0, dv * 1000, alpha=.25)
    axes[2].set(xlabel="容量 [Ah]", ylabel="ΔV = V充 - V放 [mV]",
                title=f"迟滞电压（中间 80% 均值 {np.mean(dv_mid)*1000:.0f} mV）")
    axes[2].grid(alpha=.3)

    fig.tight_layout()
    path = common.savefig(fig, "cycle_lfp_1C.png")
    print(f"\nsaved {path}")

    print("\n[dQ/dV 峰位]（只在 [2.6, 3.6] V 内找峰，避开截止点处的容量陡壁）")
    lo_p, hi_p = V_BOTTOM + 0.1, V_TOP - 0.05

    def peak(Vg, d):
        m = (Vg >= lo_p) & (Vg <= hi_p)
        return float(d[m].max()), float(Vg[m][np.argmax(d[m])])

    pc, vpc = peak(Vc, dc)
    pd_, vpd = peak(Vd, dd)
    print(f"  充电 {pc:.1f} @ {vpc:.3f} V")
    print(f"  放电 {pd_:.1f} @ {vpd:.3f} V")
    print(f"  峰位差 {vpc - vpd:+.3f} V")
    print(f"  迟滞电压（中间 80%）{np.mean(dv_mid)*1000:.0f} mV")

    print("\n[注意] CE≈100% 是因为本模型未开任何副反应（无 SEI/析锂/CEI）。")
    print("       要得到真实 <100% 的 CE 与容量衰减，需开 SEI 等退化选项，")
    print("       并注意 Prada2013 缺少 SEI 参数，需要另外补齐。")


if __name__ == "__main__":
    main()

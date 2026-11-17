"""2D 软包电芯快速仿真：量化面内（y-z）不均匀性对容量 / 放电曲线的影响。

用法::

    .\.venv\Scripts\python.exe pouch2d.py

同一电芯、同一工况，对比：
  A. 1D 均匀   —— 集流体电位不解析，等价"电流均匀注入每个位置"
  B. 2D 电位对 —— 解析集流体面内电位/电流分布，带极耳几何

并扫描集流体面内电子电导（1x → 1/16x）与倍率（1C / 3C），找不均匀性开始
真正咬住容量的门槛。这是"这枚电芯的非均匀性要不要紧"的最省成本筛选。

实测踩到的两个坑，写在这里免得后人重踩：
  1. Marquis2019 自带下截止 3.1 V，放电会在 3.1 V 停住，不是 3.0 V。
     所以统一用该截止电压下的容量做对比，不硬拉到 3.0。
  2. 界面电流密度是 (x, y, z, t) 四维，且同一数组里前半是正极、后半是负极
     （符号相反）。算面内不均匀度必须先按电极拆开，否则会算出 2000% 的假数字。
  3. 放大尺寸不能只改 Electrode width/height —— 极耳坐标是绝对量，会报
     "tab location not valid"。要换大电芯请换参数集（如 NCA_Kim2011）。
"""
from __future__ import annotations

import time

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


def make_sim(dimensionality: int, sigma_scale: float = 1.0, c_rate: float = 1.0,
             param_set: str = PARAM_SET):
    model = pybamm.lithium_ion.SPMe(
        {
            "thermal": "x-lumped",
            "cell geometry": "pouch",
            "current collector": "potential pair",
            "dimensionality": dimensionality,
        }
    )
    pv = pybamm.ParameterValues(param_set)
    pv["Current function [A]"] = c_rate * pv["Nominal cell capacity [A.h]"]
    if sigma_scale != 1.0:
        for side in ("Negative", "Positive"):
            key = f"{side} current collector conductivity [S.m-1]"
            pv[key] = pv[key] * sigma_scale
    return pybamm.Simulation(model, parameter_values=pv, solver=pybamm.IDAKLUSolver())


def inplane_nonuniformity(sol) -> tuple[float, float, float]:
    """返回 (正极峰峰/均值 %, 负极峰峰/均值 %, 面内温差 K)。"""
    j = sol["Interfacial current density [A.m-2]"].entries
    if j.ndim != 4:
        return np.nan, np.nan, np.nan
    nx = j.shape[0]
    pos = np.abs(j[: nx // 2, ..., -1]).mean(axis=0)  # 压掉 x 维，留 (y,z)
    neg = np.abs(j[nx // 2:, ..., -1]).mean(axis=0)

    def spread(a):
        a = a[np.isfinite(a)]
        return (a.max() - a.min()) / abs(a.mean()) * 100.0 if a.size else np.nan

    T = sol["X-averaged cell temperature [C]"].entries[:, :, -1]
    return spread(pos), spread(neg), float(T.max() - T.min())


def run(**kw):
    sim = make_sim(**kw)
    t0 = time.time()
    sol = sim.solve([0, 20000])
    wall = time.time() - t0
    out = {
        **kw,
        "wall": wall,
        "cap": float(np.ravel(sol["Discharge capacity [A.h]"].entries)[-1]),
        "t_end": float(np.ravel(sol["Time [s]"].entries)[-1]),
        "V_end": float(np.ravel(sol["Voltage [V]"].entries)[-1]),
        "T_mean": float(np.ravel(sol["X-averaged cell temperature [C]"].entries).mean()),
    }
    out["nu_pos"], out["nu_neg"], out["dT"] = inplane_nonuniformity(sol)
    out["sol"] = sol
    return out


def main():
    print("=" * 84)
    print(f"2D 软包 vs 1D 均匀  参数={PARAM_SET}  1C 放电（自带 3.1 V 截止）")
    print("=" * 84)

    base = run(dimensionality=1)
    print(f"[1D 均匀]        {base['wall']:5.1f}s  cap={base['cap']:.4f} Ah  "
          f"t_cutoff={base['t_end']:.0f}s  T={base['T_mean']:.1f}C")

    print("\n--- 倍率扫描（2D, sigma x1）---")
    rate_rows = []
    for cr in (1.0, 2.0, 3.0):
        b = run(dimensionality=1, c_rate=cr)
        r = run(dimensionality=2, c_rate=cr)
        dcap = (r["cap"] / b["cap"] - 1) * 100
        rate_rows.append((cr, b, r, dcap))
        print(f"  {cr:g}C: 1D cap={b['cap']:.4f} | 2D cap={r['cap']:.4f} "
              f"({dcap:+.3f}%)  t={r['t_end']:.0f}s  面内温差={r['dT']:.2f}K  "
              f"不均匀度(+/−)={r['nu_pos']:.1f}%/{r['nu_neg']:.1f}%  {r['wall']:.1f}s")

    print("\n--- 集流体电导扫描（3C, 2D）---")
    sigma_rows = []
    for sc in (1.0, 0.25, 0.0625):
        r = run(dimensionality=2, c_rate=3.0, sigma_scale=sc)
        sigma_rows.append((sc, r))
        print(f"  sigma x{sc:<7g}: cap={r['cap']:.4f} Ah  t={r['t_end']:.0f}s  "
              f"面内温差={r['dT']:.2f}K  不均匀度={r['nu_pos']:.1f}%  {r['wall']:.1f}s")

    print("\n--- 电芯尺寸对比（3C, 1D vs 2D）---")
    for ps in ("Marquis2019", "NCA_Kim2011"):
        b = run(dimensionality=1, c_rate=3.0, param_set=ps)
        r = run(dimensionality=2, c_rate=3.0, param_set=ps)
        dcap = (r["cap"] / b["cap"] - 1) * 100
        print(f"  {ps:14s} 1D={b['cap']:.4f} | 2D={r['cap']:.4f} ({dcap:+.3f}%)  "
              f"面内温差={r['dT']:.2f}K  不均匀度={r['nu_pos']:.1f}%")

    # ---------- 图 1：放电曲线 + 不均匀性 ----------
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.3), dpi=130)

    ax = axes[0]
    for cr, b, r, _ in rate_rows:
        ax.plot(np.ravel(b["sol"]["Time [s]"].entries) / 3600,
                np.ravel(b["sol"]["Voltage [V]"].entries), "--", lw=1, label=f"{cr:g}C 1D")
        ax.plot(np.ravel(r["sol"]["Time [s]"].entries) / 3600,
                np.ravel(r["sol"]["Voltage [V]"].entries), "-", lw=1.4, label=f"{cr:g}C 2D")
    ax.set(xlabel="t [h]", ylabel="电压 [V]", title="1D 均匀 vs 2D 带极耳")
    ax.legend(fontsize=7, ncol=2)
    ax.grid(alpha=.3)

    ax = axes[1]
    crs = [x[0] for x in rate_rows]
    ax.plot(crs, [x[3] for x in rate_rows], "o-", label="容量差 (2D vs 1D)")
    ax.plot(crs, [x[2]["dT"] for x in rate_rows], "s--", label="面内温差 [K]")
    ax.set(xlabel="倍率 C", title="倍率越高，2D 效应越明显")
    ax.legend()
    ax.grid(alpha=.3)

    ax = axes[2]
    scs = [x[0] for x in sigma_rows]
    ax.plot(scs, [x[1]["nu_pos"] for x in sigma_rows], "o-", label="正极不均匀度")
    ax.plot(scs, [x[1]["dT"] for x in sigma_rows], "d--", label="面内温差 [K]")
    ax.set_xscale("log")
    ax.set(xlabel="集流体电导倍率", title="电导差 → 不均匀")
    ax.legend()
    ax.grid(alpha=.3)

    fig.tight_layout()
    common.savefig(fig, "pouch2d_compare.png")

    # ---------- 图 2：3C 最不均匀工况的面内场 ----------
    worst = sigma_rows[-1][1]
    j = worst["sol"]["Interfacial current density [A.m-2]"].entries
    nx = j.shape[0]
    pos = np.abs(j[: nx // 2, ..., -1]).mean(axis=0)
    T = worst["sol"]["X-averaged cell temperature [C]"].entries[:, :, -1]

    fig2, axes2 = plt.subplots(1, 2, figsize=(11, 4), dpi=130)
    im0 = axes2[0].imshow(pos, origin="lower", aspect="auto", cmap="viridis")
    axes2[0].set(title=f"正极面内电流密度 (sigma x{sigma_rows[-1][0]:g}, 3C)",
                 xlabel="z 网格", ylabel="y 网格")
    fig2.colorbar(im0, ax=axes2[0], label="A/m2")
    im1 = axes2[1].imshow(T, origin="lower", aspect="auto", cmap="inferno")
    axes2[1].set(title="面内温度分布 [C]", xlabel="z 网格", ylabel="y 网格")
    fig2.colorbar(im1, ax=axes2[1], label="C")
    fig2.tight_layout()
    common.savefig(fig2, "pouch2d_fields.png")

    print("\nsaved pouch2d_compare.png, pouch2d_fields.png")


if __name__ == "__main__":
    main()

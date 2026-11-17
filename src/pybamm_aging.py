"""PyBaMM 循环老化：输出与 POLIDEMO 同口径的 SOH / LLI / LAM / 膨胀，便于交叉验证。

    python pybamm_aging.py [cycles]

模型：SPMe + ec-reaction-limited SEI + swelling-and-cracking 颗粒力学 + stress-driven LAM
参数：OKane2022（PyBaMM 内置、带完整老化参数的 5 Ah 电芯）

对应 POLIDEMO 的量：
    SOH      <- 放电容量 / 初始放电容量
    LLI      <- Loss of capacity to SEI(+cracks) / 初始容量
    LAM_p/n  <- Loss of active material in positive/negative electrode [%]
    膨胀     <- 颗粒体积变化 -> 厚度变化（POLIDEMO 的 irr_swelling 是可逆+不可逆；
               PyBaMM 这里给的是颗粒尺度体积膨胀，不是电芯厚度，口径不同，见文末说明）
"""
from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pybamm  # noqa: E402
import common  # noqa: E402

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False
pybamm.set_logging_level("ERROR")

CYCLES = int(sys.argv[1]) if len(sys.argv) > 1 else 50
C_RATE = 1.0


def build_simulation(parameter_values):
    # 只用稳定的物理组合：'swelling and cracking' 的裂纹 ODE 在 OKane2022 + IDAKLU 下
    # 通常在 1~11 循环后步长塌缩到 hmin 并暴毙（见文末说明），因此改用 swelling only，
    # 仍保留 SEI 生长 + stress-driven LAM 两条老化通道。
    model = pybamm.lithium_ion.SPMe(
        {
            "SEI": "ec reaction limited",
            "particle mechanics": "swelling only",
            "loss of active material": "stress-driven",
            "thermal": "lumped",
        }
    )
    # 先放后充：电芯从 100% SOC 起步（PyBaMM 默认初始 SOC 由参数决定），
    # 首循环即一段完整放电，容量可直接作为 SOH 基准。
    experiment = pybamm.Experiment(
        [f"Discharge at {C_RATE}C until 2.5 V", "Rest for 5 minutes",
         f"Charge at {C_RATE}C until 4.2 V", "Rest for 5 minutes"] * CYCLES
    )
    return pybamm.Simulation(
        model, experiment=experiment, parameter_values=parameter_values,
        solver=pybamm.IDAKLUSolver(),
    )


def summarize(sol):
    """逐循环提取容量与各老化通道，返回 (N, SOH, LLI%, LAM_p%, LAM_n%)。

    注意：Experiment 的每个 step（放电/静置/充电/静置）各产生一个 sub_solution，
    所以 sol.cycles 是 *step* 序列而非循环序列（4N 个）。用 termination 事件识别
    放电步（"Voltage < 2.5 V"），比按位置猜测稳健。
    """
    n, soh, lli, lam_p, lam_n = [], [], [], [], []
    cap0 = None
    cycle_no = 0

    for step in sol.cycles:
        if "2.5 [V]" not in str(step.termination):
            continue  # 静置/充电步，跳过
        cycle_no += 1
        cap = float(np.ravel(step["Discharge capacity [A.h]"].entries)[-1])
        if cap0 is None:
            cap0 = cap

        def last(var):
            try:
                return float(np.ravel(step[var].entries)[-1])
            except KeyError:
                return 0.0

        # LLI = 所有锂损失通道之和（SEI、裂纹 SEI、LAM 封死的锂）
        # 前四项是 [A.h]，后两项是 [mol]，用 F 换成 A.h（1 A.h = 3600 C）
        lli_total = sum(last(v) for v in [
            "Loss of capacity to negative SEI [A.h]",
            "Loss of capacity to positive SEI [A.h]",
            "Loss of capacity to negative SEI on cracks [A.h]",
            "Loss of capacity to positive SEI on cracks [A.h]",
        ]) + sum(last(v) for v in [
            "Loss of lithium due to loss of active material in negative electrode [mol]",
            "Loss of lithium due to loss of active material in positive electrode [mol]",
        ]) * 96485.0 / 3600.0
        n.append(cycle_no)
        soh.append(cap / cap0)
        lli.append(100.0 * lli_total / cap0)
        lam_n.append(last("Loss of active material in negative electrode [%]"))
        lam_p.append(last("Loss of active material in positive electrode [%]"))

    lli = np.array(lli)
    # OKane2022 自带一段"初始 SEI"（约 0.16 A.h 的平台），是参数标定基线而非循环损伤，
    # 所以 LLI 以第 1 循环为零点报增量，绝对值另存。
    return (np.array(n), np.array(soh), lli - lli[0], np.array(lam_p), np.array(lam_n),
            lli[0])


def main():
    params = pybamm.ParameterValues("OKane2022")
    sim = build_simulation(params)

    import time

    t0 = time.time()
    sol = sim.solve()
    wall = time.time() - t0

    n, soh, lli, lam_p, lam_n, lli_base = summarize(sol)
    print(f"cycles={int(n[-1])}  wall={wall:.1f}s  ({wall / max(n[-1], 1):.2f}s/cycle)")
    print(f"末端: SOH={soh[-1]:.4f}  LLI增量={lli[-1]:+.3f}% "
          f"(基线 {lli_base:.2f}%)  LAM_n={lam_n[-1]:.3f}%  LAM_p={lam_p[-1]:.3f}%")

    fig, axes = plt.subplots(2, 2, figsize=(11, 7), dpi=130)
    axes[0, 0].plot(n, soh, "o-", ms=3, lw=1.2)
    axes[0, 0].set(title="放电容量保持率 SOH", xlabel="循环数", ylabel="SOH")
    axes[0, 1].plot(n, lli, "s-", ms=3, lw=1.2, color="tab:red")
    axes[0, 1].set(title="锂库存损失 LLI 增量（相对第 1 循环）", xlabel="循环数", ylabel="ΔLLI [%]")
    axes[1, 0].plot(n, lam_p, "^-", ms=3, lw=1.2, label="LAM$_p$")
    axes[1, 0].plot(n, lam_n, "v-", ms=3, lw=1.2, label="LAM$_n$")
    axes[1, 0].set(title="活性材料损失 LAM", xlabel="循环数", ylabel="LAM [%]")
    axes[1, 0].legend()
    axes[1, 1].plot(n, 1 - soh, "d-", ms=3, lw=1.2, color="tab:green")
    axes[1, 1].set(title="总容量损失 1-SOH", xlabel="循环数", ylabel="1-SOH")
    for ax in axes.ravel():
        ax.grid(alpha=0.3)
    fig.suptitle(
        f"PyBaMM SPMe + SEI + LAM (OKane2022, 5 Ah)  {C_RATE}C 循环  {wall:.0f}s",
        fontsize=10,
    )
    fig.tight_layout()
    common.savefig(fig, "pybamm_aging.png")
    print(f"saved {common.results_dir() / 'pybamm_aging.png'}")


if __name__ == "__main__":
    main()

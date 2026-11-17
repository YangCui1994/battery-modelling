"""POLIDEMO 复现演示：直接从 exe 字节码加载模型，跑一段寿命并画出 SOH / LLI / LAM / 膨胀。

    python demo_lifetime.py            # 默认 800 循环，ΔN=50，capped 跳跃
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import common  # noqa: E402
import polidemo_from_exe  # noqa: E402

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

polidemo_from_exe.load()
from polidemo import ModelParams, run_lifetime  # noqa: E402

MAX_CYCLES = 800
DELTA_N = 50

params = ModelParams()
t0 = time.time()
res = run_lifetime(
    params, max_cycles=MAX_CYCLES, delta_n=DELTA_N, jump_mode="capped", use_cycle_jump=True
)
wall = time.time() - t0

n = res.cycle_number
soh, lli, lam_p, lam_n = res.soh, res.lli, res.lam_p, res.lam_n
swell = res.irr_swelling
simulated = len(res.records)
jumped = sum(j[2] for j in res.jumps)

print(f"stop={res.stop_reason}  真实求解循环={simulated}  跳跃累计={jumped}  用时={wall:.1f}s")
print(f"末端: N={n[-1]:.0f}  SOH={soh[-1]:.4f}  LLI={lli[-1]:.2f}%  LAM_n={lam_n[-1]:.2f}%  swelling={swell[-1]:.4f} mm")

fig, axes = plt.subplots(2, 2, figsize=(11, 7), dpi=130)
axes[0, 0].plot(n, soh, "o-", ms=3, lw=1.2)
axes[0, 0].set(title="放电容量保持率 SOH", xlabel="循环数", ylabel="SOH")
axes[0, 1].plot(n, lli, "s-", ms=3, lw=1.2, color="tab:red")
axes[0, 1].set(title="锂库存损失 LLI", xlabel="循环数", ylabel="LLI [%]")
axes[1, 0].plot(n, lam_p, "^-", ms=3, lw=1.2, label="LAM$_p$")
axes[1, 0].plot(n, lam_n, "v-", ms=3, lw=1.2, label="LAM$_n$")
axes[1, 0].set(title="活性材料损失 LAM", xlabel="循环数", ylabel="LAM [%]")
axes[1, 0].legend()
axes[1, 1].plot(n, swell, "d-", ms=3, lw=1.2, color="tab:green")
axes[1, 1].set(title="不可逆膨胀", xlabel="循环数", ylabel="厚度 [mm]")
for ax in axes.ravel():
    ax.grid(alpha=0.3)
fig.suptitle(
    f"POLIDEMO (22 Ah LCO/石墨, C/2 充 1C 放)  ΔN={DELTA_N}, capped 跳跃  "
    f"{simulated} 次真实循环 → {MAX_CYCLES} 循环, {wall:.0f} s",
    fontsize=10,
)
fig.tight_layout()
common.savefig(fig, "polidemo_soh.png")
print(f"saved {common.results_dir() / 'polidemo_soh.png'}")

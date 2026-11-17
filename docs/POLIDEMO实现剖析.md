# POLIDEMO 实现剖析（Windows 版 1.4.0 / Python 3.13）

> 工作目录：`D:\2026_agent_work\12_battery_modelling`
> 对象：`POLIDEMO-windows\POLIDEMO.exe`（16.5 MB，PyInstaller 打包）+ 论文 *Applied Energy* 404 (2026) 126744。
> 说明：你说的 “rae” 我按“读/拆这份实现”理解；如果你指的是某个工具或 agent，告诉我，我再按那个来。

---

## 0. 三句话结论

1. **这套 exe 不是论文配套的 MATLAB 原码**，而是把 MATLAB 版 POLIDEMO 完整重写成 Python 的**移植版**（v1.4.0），再套一层 PySide6 图形界面、用 PyInstaller 打包；源码已不在本机，全部以字节码形式躺在 exe 的 `PYZ.pyz` 里。
2. 它由 **16 个模块**组成：`params / data / materials / ocv → aging / mechanics → spm → runner → gui / export`，物理内核是**单颗粒模型（SPM）+ 扩散诱导应力（DIS）+ SEI/CEI/析锂/LAM/不可逆膨胀**，外加论文 Algorithm 1 的**循环跳跃**加速。
3. 我把字节码原样重装成可导入的包并跑通了，单循环结果与官方 `--selftest` **逐位一致**（20.2453 Ah / 0.00034 mm / 11150.2 s），所以下面每个公式都是从真正运行的代码里读出来的，不是从论文倒推的。

---

## 1. 这个发行包里有什么

| 路径 | 作用 |
|---|---|
| `POLIDEMO.exe` | 入口脚本 `run_gui.py`；带 `--selftest` 分支，不打开界面跑一个循环并写 `output/selftest.txt` |
| `_internal/` | 冻结的解释器（python313.dll）+ 依赖：numpy 2.5.4、scipy 1.18.1、pandas 3.1.1、matplotlib 3.11.2、PySide6、Pillow |
| `data/aging_data_set.mat` | 内置实验退化数据（LCO–石墨 22 Ah 软包，1125 循环） |
| `materials/*.csv` | OCP 正/负极、偏摩尔体积 Ω 正/负极、单周期实验、寿命实验四类模板 |
| `output/` | 导出 CSV/Excel/PNG、参数 JSON、自检文件 |

打包结构：`CArchive` 里只有 13 个条目，其中 `run_gui`（引导脚本）**和** `PYZ.pyz`（真正的应用包）。也就是说**整个模型 + GUI 就是 exe 内的一份 PYZ**，没有任何 `.py` 源文件随包。

---

## 2. 拆出来的模块地图

| 模块 | 职责 | 关键函数 |
|---|---|---|
| `polidemo.params` | 106 个数据类字段的全部输入参数（对应 `Parameters_init.m`）+ 循环间演化的 `AgingState` | `ModelParams`、`AgingState`、`ProtocolStep`、`init_aging_state`、`dn_from_rate` |
| `polidemo.data` | 读 `aging_data_set.mat` / 用户 CSV / Excel 的实验退化数据 | `load_experimental`、`load_lifetime_experiment` |
| `polidemo.materials` | 读 OCP / Ω / 单周期实验的曲线文件 | `load_ocp_table`、`load_curve_table`、`load_experimental_cycle` |
| `polidemo.ocv` | 内置 LCO/石墨 OCP 经验式、Ω 分段线性积分、自定义曲线的 PCHIP 插值 | `builtin_positive_ocp`、`builtin_negative_ocp`、`OcpPair`、`omdc_positive/negative` |
| `polidemo.aging` | 退化子模型：SEI/CEI/析锂通量、LAM 疲劳律、LLI、不可逆膨胀 | `sei_flux`、`lam_volume_fraction`、`irreversible_swelling` |
| `polidemo.mechanics` | 球颗粒扩散诱导应力解析解、应力强度因子、Paris 律、可逆厚度 | `compute_stress_spl`、`stress_intensity_factor`、`crack_propagation` |
| `polidemo.spm` | 内核：空间离散、状态布局、右端函数、事件、后处理 | `discretize`、`StepSolver`、`simulate_step`、`_post_process` |
| `polidemo.runner` | 循环/全寿命调度 + 循环跳跃 | `run_cycles`、`run_lifetime`、`cycle_jump` |
| `polidemo.export` | CSV / Excel / 参数 JSON | `export_lifetime_excel` … |
| `polidemo.gui.*` | PySide6 界面：主窗、参数面板、绘图、工作线程、内置帮助页 | `MainWindow`、`ParamEditor`、`LifetimePlot`、`SimWorker`、`build_help_html` |

依赖方向是一条直线，没有环：

```
params ─┬─> ocv ──┐
        ├─> aging ─┼─> spm ─> runner ─> gui
mechanics ─────────┘            └────> export
```

最有价值的一条线索：`gui/help_page.py` 里内置了一份 HTML 帮助页，**公式编号直接沿用论文 (1)–(37) 和 (D.1)–(D.3)**，并逐条标注了“代码口径”（即与论文/原 MATLAB 不一致的地方）。这份帮助页是作者自己写的实现说明，和字节码对得上。

---

## 3. 一个工步是怎么解的

### 3.1 状态向量

`StateLayout` 管一个**完整状态向量**，默认工况下 `size = 24`、`n_dyn = 23`（只有 `j` 的槽位不参与积分）：

```
[cp(N+1) , cn(N+1) , L_sei , a , L_cei , L_plating , jn , jp , jn_tot , jp_tot]
```

- `cp / cn`：正/负极球形颗粒内 N 个有限体积节点的固相锂浓度，默认 `N = 10`；
- `L_sei / L_cei / L_plating`：三层膜厚（CEI、析锂默认关）；
- `a`：裂纹长度（**只有 `crack_mechanism ∈ {0,1}` 才进状态**；默认 `= 2` 时 `has_crack_length_state = False`，`i_a is None`）；
- `j*` 是热启动用的携带量，不参与积分。

跨工步/跨循环传递时打成**命名字典**（`pack_state` / `build_full`），网格数变了会按 `n_p` 截断，这也是为什么改 `N` 不会把状态搞坏。

### 3.2 求解流程（`simulate_step`）

```python
lay    = layout_for(params, mode)              # mode: 1=恒流/静置, 2=恒压
solver = StepSolver(params, aging, mode, crate, current_density, v_stop, v_hold, i_stop)
y0     = initial_state(params, aging, mode, soc, current_density, prev_state)
t_eval = np.arange(t0, tf + 1e-9, time_step)   # 默认 10 s 一个输出点，超过 4000 点改 linspace
sol    = solve_ivp(solver.rhs, (t0, tf), y0[lay.dyn_indices],
                   method='BDF', t_eval=t_eval, events=solver.events(),
                   rtol=1e-10, atol=1e-12, dense_output=True)
# 事件发生后把精确末点补进输出，再交给 _post_process
```

数值设置（都是默认值，界面可改）：

| 项 | 值 |
|---|---|
| 空间离散 | 有限差分，`N = 10`，`dr = 1/N` |
| 时间积分 | `BDF`，`rtol = 1e-10`，`atol = 1e-12`，输出步长 `10 s`，`max_points = 4000` |
| 事件 | 电压到达设定截止值 / 电流降至设定下限 / 正极表面锂浓度归零 / 负极表面锂浓度归零（前两个按工步类型出现） |
| CV 步的电流 | 外层对 `jn_tot` 求根：`brentq(xtol=1e-24, rtol=1e-14, maxiter=200)`，先做最多 50 次区间倍增找变号 |

### 3.3 每个时刻算了什么（`StepSolver.evaluate`）

```python
cs_p_s, cs_n_s = cp[-1], cn[-1]                       # 表面浓度 = 最外节点
u_p, u_n       = ocp(cs_p_s/cs_maxp, cs_n_s/cs_maxn, crate)   # 注意：用表面浓度，不是平均 θ
i0_p, i0_n     = exchange_current_density(...)        # i0 = k·F·sqrt(ce)·sqrt(cs)·sqrt(cs_max-cs)
jp_tot, jn_tot = -I/(F·Ap_int),  I/(F·An_int)         # 按面积分配的总通量
jn_int         = _solve_jn(...)                       # 扣掉 SEI + 析锂后的真实嵌锂通量
jp_int         = _solve_jp(...)                       # 扣掉 CEI
eta_p, eta_n   = overpotential(jp_int, i0_p), overpotential(jn_int, i0_n)   # η = 2RT/F·asinh(jF/2i0)
V              = u_p - u_n + eta_p - eta_n - R_int·I
```

几个容易看错、但很关键的实现细节：

- **OCP 用表面化学计量比**（`cs_surf/cs_max`），不是平均 θ；平均 θ 只用于析锂判据和输出。
- **`_solve_jn / _solve_jp` 不是牛顿迭代，是定常迭代（Picard）**：最多 16 次，判据 `|Δ| ≤ 1e-13·max(|新值|,1e-30) + 1e-25`，不收敛也不报错，直接返回最后一次值。`_solve_jp` 还会按 `x > 0` 决定 `jp_tot ± j_cei`（与 `_solve_jn` 的无条件相减不对称）。
- **通量分两层**：`*_tot` 是外电路要求的，`*_int` 是扣掉副反应后真正嵌锂的；只有 `layout` 里有对应槽位时才做副反应分配。
- **CV 步**：`evaluate` 内部每次都要跑一次 `brentq`，失败时**静默返回 0**（等价开路），不抛异常。

实测一组数（默认参数、1C 放电第一步、`t = 0`）：

| 量 | 值 |
|---|---|
| `U_p / U_n` | 3.8978 V / 0.7102 V |
| `η_p / η_n` | −0.0168 V / 0.1464 V |
| `R_int` | 0.004 Ω·m² |
| `V` | 2.8910 V（正好 = 3.8978 − 0.7102 − 0.0168 − 0.1464 − 0.004×33.353） |

### 3.4 工步结束后（`_post_process`）

1. 用 `dense_output` 的输出点**逐点再调一次 `evaluate`**（CV 步会因此再跑 `brentq`），重算 V、η、通量、应力等 46 个字段；
2. `compute_stress_spl` 逐点算 σ_r/σ_c（当 `enable_sei_on_cracks or enable_lam`）；
3. 容量口径分两种：**CC 步用名义值 `|crate|·22 Ah·t/3600`，只有 CV 步才真正对电流积分**；
4. `SOC` 是整步一个常数（由末端 `θ_n` 线性映射），不是逐点 SOC；
5. 计算 LLI / LAM / 不可逆膨胀 / 相对厚度变化，并 `aging.copy()` 出快照；
6. **更新退化状态**：充电步记 `σ_c_max`、放电步记 `σ_c_min`（取 `[1:, -1]`，只用表面节点且要求电流严格同号），然后按疲劳律更新 `vol_fraction_solidphase`，再更新 `A_crack`（机制 2）或 `N_cracks`（机制 1）。

> 注意：`aging.cycle_count` 在 `spm` 里**只读不写**，循环号是 `runner` 写进去的。

---

## 4. 退化子模型：代码里的实际公式

以下都是字节码可证、且与内置帮助页描述一致的。

### 4.1 SEI 生长（`aging.sei_flux` + `spm.rhs`）

```
η_SEI = η_n + U_n(θ_n) − U_SEI
J_SEI = − n_SEI·c_solv / ( L_SEI/D_solv + 1/(k_SEI·exp(−0.5·n_SEI·F·η_SEI/(R_g·T))) )
dL_SEI/dt = −0.5·V_m,SEI·J_SEI · A_int,n / ( A_int,n + 2·N_cracks·(da/dt)·w_crack )
```

- 当 `−0.5·n·F·η/(RT) < −300` 时把动力学项置 `+∞`，退化为**纯扩散控制**（防止 `exp` 溢出）；
- `A_int,n` 含裂纹面积：`a_s,n·t_n·A_n + A_cracks`，裂纹面积只加在**负极**；
- 分母里 `2·N_cracks·(da/dt)·w_crack` 是“裂纹张开露出的新断面”，新断面继续长 SEI，所以有 0.5 的厚度折半。

### 4.2 CEI 与析锂（beta，默认关）

```
η_CEI = η_p + U_p(θ_p) − U_CEI     仅当 (U_p + η_p) > 0 才生长
J_CEI = k_CEI·c_e·exp(−0.5·F·η_CEI/(R_g·T))        dL_CEI/dt = J_CEI·V_m,CEI

J_plating = 0                      当 jn_int ≥ 0（即非充电）
否则 k_dy = (c_s,n^surf − c_s,n^avg·c_s,max,n)/c_s,max,n
     J_plating = −k_plating·c_e·k_dy·exp(−0.65·F·η_plating/(R_g·T))
     dL_plating/dt = −J_plating·V_m,Li
```

### 4.3 活性材料损失 LAM（疲劳律）

```
ε_p = ε_p0 − (1/(n_LAM_p+1))·a_LAM_p·max(Δσ_p,0)^{m_LAM_p}·N^{n_LAM_p+1}
ε_n = ε_n0 − (1/(n_LAM_n+1))·a_LAM_n·max(Δσ_n,0)^{m_LAM_n}·N^{n_LAM_n+1}
```

`Δσ` 是本循环内 `σ_c,max − σ_c,min`。`vol_fraction_solidphase` 有 `1e-4` 下限。默认 `n_LAM_p = 0`、`m_LAM_p = 0.19`；负极 `n = 5.55`、`m = −0.7`——负极这条才是让容量曲线在后期出现**膝点**的关键。

裂纹面积两种口径：

```
机制 0/1（Paris 律）：A_cracks = 2·a·N_cracks·w_crack ，da/dt = (C/t_n)·(ΔK)^m
机制 2（默认）：      A_cracks = k_crack·(ε_n0 − ε_n)/ε_n0      （k_crack = 1.1）
```

### 4.4 内阻与两个百分比指标

```
R_int = R_f + L_SEI/σ_SEI + L_CEI/σ_CEI + L_plating/σ_plating
      + k_LAM,p·(ε_p0−ε_p)/ε_p0 + k_LAM,n·(ε_n0−ε_n)/ε_n0        [Ω·m²]

LLI   = (n_Li,0 − n_Li)/n_Li,0 × 100%     n_Li = (θ_p·C_p + θ_n·C_n)·3600/F
LAM_k = (C_k,0 − C_k)/C_k,0 × 100%        C_k ∝ ε_k·t_k·c_s,max,k·F·A·N_lay/3600
```

`n_Li,0` 在第 1 循环末锁定（`aging.n_Li_initial`）。

### 4.5 不可逆膨胀（`aging.irreversible_swelling`）

```
ε_v,SEI = ( 3ε_n·t_n·A_n/R_n + A_cracks )·L_SEI / (A_n·t_n)
n_SEI   = ( 3ε_n·t_n·A_n/R_n + A_cracks )·L_SEI·N_lay / V_m,SEI
V_el    = 2·n_SEI·6.67e-5
b_eqv   = V_el/A + k_z·101300·t_nom
c_eqv   = −k_z·n_SEI·R_g·T·t_nom/A + k_z·101300·t_nom·V_el/A
t_gas   = 1000·(−b + √(b²−4c))/2
t_SEI   = 1000·ε_v,SEI·N_lay·t_n
t_irr   = t_SEI + t_gas − 1000·V_el/A        [mm]
```

即：SEI 占体积 → 产气顶开 → 扣掉被消耗的电解液体积，三项相加就是不可逆厚度。

---

## 5. 力学：扩散诱导应力（DIS）解析解

`mechanics.compute_stress_spl(x, cs_p, cs_n, params)`：

1. 把浓度剖面拟合成 **`CubicSpline(x, c(r)·r²)`**（not-a-knot，等价 MATLAB `spline`），再取 `antiderivative()` 做解析积分；
2. 用热弹性类比解：

```
mec_bc = Ω/(1−ν)·2(1−2ν)·∫₀^R c·r²dr
base   = E·mec_bc/(3(1−2ν))
const  = ΩE/(3(1−ν))        consth = 2ΩE/(9(1−ν))

σ_r(R) = 0（自由表面）,  σ_r(0) = σ_c(0) = base − consth·c(0)
σ_r(r) = base − 2·const·∫₀^r c·r²dr / r³
σ_c(r) = base − const·c(r) + const·∫₀^r c·r²dr / r³
```

3. 应力强度因子（表面裂纹，`q = a/R_n`）：

```
K_max = √a · Σ_i c_i/1e6 · (1 − q^i) · y(q)         y(q) = 0.1864q² + 0.4987q + 0.5204…（多项式系数表）
K_min = 0（忽略压缩段）
da/dt = (C/t_n)·(K_max − K_min)^m                    C = 2.85e-6, m = 2.15, t_n = 10800 s
```

`c_i` 是把 σ_c 沿半径做 6 阶多项式拟合再反转系数的结果——这套解析 SIF 来自作者自己的论文（Eng. Fract. Mech. 2023, 292:109597）。

---

## 6. 全寿命怎么跑：段式调度 + 循环跳跃

### 6.1 调度

```
run_lifetime(params, max_cycles=1200, delta_n=50, cycles_per_segment=3, jump_mode='adaptive')
  ├─ 第 1 段：run_cycles(n=3, cycle_count=1, prev_state=None)
  └─ while 未停：
        cycle_jump(...)          # 可选：一次跳过 ΔN 个循环（不真算）
        run_cycles(n=3, ...)     # 真算 3 个循环
```

所以实际求解的是 `1,2,3` → 跳到 `54,55,56` → 跳到 `107,108,109` → … 每个“段”真算 3 个循环，段间用 `CycleRecord.final_state` 续算。实测序列：`1,2,3,54,55,56,107,108,109,...`。

### 6.2 跳跃判据（论文 (D.1)–(D.3)，`runner.cycle_jump`）

用最近 3 个循环的放电末值，对 **6 个分量**各算一个 ΔN，取最小：

| 分量 | 公式 |
|---|---|
| `L_SEI`、`L_CEI`、`L_plating` | `tol·Δ₁/Δ₂`（Δ₁ 一阶差分，Δ₂ 二阶差分；`Δ₂ ≤ 0` 视为 ∞） |
| `a`（裂纹长度） | 同式，但 **tol 放大 10 倍** |
| `ε_p`、`ε_n` | `tol·|ε|/|Δε|`（相对容差） |

- `tol = params.tol_jump_size = 0.0008`；
- **只有 `cycle_count > 50` 才启用容差公式**，之前按用户给的 ΔN；
- 取最小后取整，**下限 5 个循环**；
- 外推是一阶的：`x_{N+ΔN} = x_N + ΔN·(x_N − x_{N−1})`，同步外推 4 个 `σ_c_max/min`，然后用新循环号重算 LAM、`A_crack`/`N_cracks`；
- 三种模式：`adaptive`（原码行为）、`capped`（min(容差步长, ΔN)）、`fixed`（永远 ΔN，最快但膝点附近最不准）。

### 6.3 实测加速效果

`demo_lifetime.py`（800 循环、ΔN=50、capped）：

| 指标 | 结果 |
|---|---|
| 真实求解循环 | **153**（其余 647 个循环是跳过的） |
| 墙钟时间 | 301 s（单循环约 1.1 s，一次性 BDF 求解） |
| 末端 SOH | 0.9399 |
| 末端 LLI | 4.20% |
| 末端 LAM_n | 0.85% |
| 末端不可逆膨胀 | 0.2695 mm |

曲线见 `polidemo_soh.png`（SOH / LLI / LAM / 膨胀 四联图）。

---

## 7. 默认参数（22 Ah LCO–石墨软包）

完整 106 项见 `polidemo_params_defaults.tsv`。核心摘录：

| 类别 | 参数 | 默认值 |
|---|---|---|
| 几何 | `pouch_length/width/thickness` | 0.1745 / 0.0945 / 0.0092 m，40 层 |
| 电极 | `len_p / len_n` | 68 µm / 88 µm |
| 颗粒 | `Rp / Rn` | 2 µm / 10 µm |
| 体积分数 | `vol_frac_p / vol_frac_n` | 0.76 / 0.66 |
| 固相扩散 | `Dp / Dn` | 6e-14 / 2.5e-14 m²/s |
| 最大浓度 | `cs_maxp / cs_maxn` | 51597 / 28866 mol/m³ |
| 化学计量比 | `θ_min/θ_max`（p, n） | 0.91→0.46 / 0.0075→0.7859 |
| 动力学 | `kp / kn` | 1e-11 |
| 电解液 | `ce` | 2000 mol/m³ |
| 力学 | `Ep / En`、`νp / νn` | 191 GPa / 15 GPa，0.24 / 0.3 |
| 力学 | `om_n`、`om_p` | 4.253e-6；`om_p` 是派生属性 `−0.0232/cs_maxp ≈ −4.50e-7`（硬编码常数，不从 Ω 表来） |
| SEI | `k_SEI`、`D_solv`、`c_solv`、`n_SEI` | 2.7e-18、4e-16、4541、1.0 |
| LAM | `a_LAM_p/n`、`n_LAM_p/n`、`m_LAM_p/n` | 2.69e-6 / 1.2e-15、0 / 5.55、0.19 / −0.7 |
| 跳跃 | `tol_jump_size` | 0.0008 |
| 其它 | `F = 96487`（注意：不是 96485）、`Tref = 294.6 K` | |

工况默认是论文那套三步：**CC 充电 −0.5C 到 4.2 V → CV 充电到 C/20 → CC 放电 1C 到 3.0 V**。

---

## 8. 与论文 / MATLAB 原码的已知差异

这是作者自己在帮助页里标注的“代码口径”，我逐条对过字节码，都能对上：

| 项 | 论文 | 代码 |
|---|---|---|
| 固相扩散 | 式 (2) 含应力驱动项 | 只解纯扩散（忽略应力梯度项） |
| 化学应变 | 常数 Ω 与 ∫Ωdc 两种 | 应力用常数 `Ω`；可逆厚度用 `∫Ω dc`（所以换 Ω 表只影响膨胀，不影响应力） |
| LAM 更新时机 | post-processing | 只在**放电步末**更新：`np.all(current > 0)`（模型中电流 >0 = 放电）的步更新 `σ_c_max` 并触发 LAM；`np.all(current < 0)` 的充电步只更新 `σ_c_min` |
| 温度 | 有热耦合 | **未实现**：`enable_temperature=True` 直接抛 `NotImplementedError`（我验证过） |
| CEI / 析锂 | beta 功能 | 实现完整，默认关闭 |
| 裂纹面积 | 式 (27)–(29) 三种 | 三种都实现，默认 `crack_mechanism = 2`（式 29，无需解 Paris 律） |
| CV 步电流 | 外层求解 | 内层 `brentq`，且失败静默返回 0 |
| 容量口径 | 电荷积分 | CC 步用 `|crate|·22 Ah·t/3600` 名义值，CV 步才积分 |
| 跳跃步长取整 | 论文写 ceil | 代码是 `int()` 截断 + 下限 5 |

另外还有两个 Python 侧的实现特征（不是差异，但看代码时容易踩）：

- `pack_state`/`build_full` 是按**名字**传状态的，`jp_tot` 在末态回填时只赋给局部变量没写回（`full_jp_tot` 赋值后未使用），所以跨步的 `jp_tot` 基本恒为携带值；
- `np.trapezoid` 是 NumPy ≥ 2.0 的名字，换到 NumPy 1.x 会 `AttributeError`。

---

## 9. 怎么直接用它（不用装 MATLAB，也不用反编译）

我写了一个加载器，直接从 exe 的 PYZ 里把包装进 `sys.modules`：

```python
import polidemo_from_exe
polidemo_from_exe.load(r"POLIDEMO-windows\POLIDEMO.exe")

from polidemo import ModelParams, init_aging_state, run_cycles, run_lifetime

p = ModelParams()                      # 改参数就是改字段，例如 p.c_rate_discharge = 0.5
aging = init_aging_state(p)
recs = run_cycles(p, aging, 1, 1)      # 跑 1 个循环
print(recs[-1].capacity_Ah)            # 20.2453 Ah，和 exe 的 --selftest 完全一致

res = run_lifetime(p, max_cycles=800, delta_n=50, jump_mode="capped")
print(res.soh[-1], res.lli[-1], res.irr_swelling[-1])
```

已验证：加载耗时 0.7 s，单循环 1.1 s，输出与官方自检逐位一致。

**局限**（要说清楚）：

- 这套东西是**字节码**，能调用、能改参数、能取所有中间量（`StepResult` 有 46 个字段），但**改公式就得改字节码**——实际做法只能是：要么按论文/MATLAB 原码重写这部分源码，要么拿到作者的 Python 源码（`https://github.com/Poli-ON/POLIDEMO` 是 MATLAB 原码，BSD-3-Clause）。
- GUI 依赖 PySide6，我这个加载器只装了计算内核（`gui` 子包没加载），所以只能写脚本用，起不了窗口。

---

## 10. 这次留下的文件

| 文件 | 说明 |
|---|---|
| `polidemo_py/` | **可读 Python 源码实现**（8 个模块 + CLI，与参考实现逐位对齐，见第 12 节） |
| `output/` | 400 循环示例输出：`lifetime.csv` / `lifetime.xlsx` / `lifetime.png` / `params.json` |
| `polidemo_from_exe.py` | 从 exe 加载 `polidemo` 包的加载器（`load()` 一行搞定） |
| `demo_lifetime.py` | 800 循环演示：跑完画 SOH/LLI/LAM/膨胀四联图 |
| `polidemo_soh.png` | 上面那次运行的图（153 次真实循环 → 800 循环，301 s） |
| `polidemo_params_defaults.tsv` | 106 个默认参数（字段 / 类型 / 默认值） |
| `_re/` | 逆向工作目录：模块清单、字节码反汇编、对拍脚本（`compare.py`），可整个删掉 |
| `_paper_text.txt` | 论文 PDF 的纯文本提取（18 页），方便检索公式 |

---

## 11. MATLAB 源码：找过了，目前拿不到

论文里有三处（摘要、2.2 节的实现说明、结论 + Data availability）都写着
`https://github.com/Poli-ON/POLIDEMO`，所以我去核了一遍，结论是**公开仓库不存在/未公开**：

| 检查 | 结果 |
|---|---|
| GitHub API `repos/Poli-ON/POLIDEMO` | **404**（同期 `Poli-ON/POLISOC` 可正常访问，说明不是网络问题） |
| GitHub 组织 `Poli-ON` 的公开仓库 | 只有 1 个：**POLISOC**（MATLAB，做 SOC 估计的另一个项目，不是 POLIDEMO） |
| GitHub 全站按仓库名搜 "POLIDEMO" | 只有 4 个无关仓库（polidemocracy / PoliDemonstrator ×2 / polidemo.github.io） |
| Wayback 机器快照 | 唯一一份 2025-09-04 的快照，仓库页只有 `LICENSE` + `README.md`，**代码从未提交** |
| 论文引用的 Zenodo 数据集 | `10.5281/zenodo.14914172` 是 LCO 老化**数据**（不是代码），且本机网络不可达 |

旁证：EVS38 的会议摘要写的是“POLIDEMO 是用 MATLAB 实现的软件，**即将**作为开源软件发布”，
与“仓库先建好、代码还没推上去”的快照状态一致。所以要么作者还没 release，要么仓库是私有的。

**想拿到原码的可行路径**（按性价比排序）：

1. 直接邮件问作者（Politecnico di Torino，Pistorio / Clerici / Somà），说明用途要 MATLAB 源码；
2. 盯 `Poli-ON/POLIDEMO` 和 `Poli-ON` 组织，等 release（论文是 2026 年 2 月刊，通常会在此后放代码）；
3. 用本报告 + 内置帮助页 + 第 12 节的 Python 实现顶上——它的数值行为和 exe 一致，且公式都有出处。

> 提醒：在代码真正公开前，引用/复现请写“模型依据论文与作者提供的 Windows 版程序”，不要写成“基于 GitHub 开源代码”。

---

## 12. 用 Python 复现整套功能（已跑通）

`polidemo_py/` 是按 MATLAB 原码结构重写的可读实现，**不依赖 exe、不依赖 MATLAB**，只用 numpy + scipy：

```
params.py     106 个参数、AgingState、ProtocolStep、默认工步
ocv.py        OCP 经验式、Ω 分段线性积分、自定义曲线 PCHIP
aging.py      SEI / CEI / 析锂 / LAM / LLI / 不可逆膨胀
mechanics.py  扩散诱导应力解析解、应力强度因子、Paris 律、可逆厚度
spm.py        有限体积离散 + solve_ivp(BDF) + 事件 + 恒压 brentq + 后处理
runner.py     run_cycles / cycle_jump / run_lifetime（论文 Algorithm 1）
export.py     CSV / Excel / JSON
plot.py       SOH-LLI-LAM-膨胀 四联图、单工步电压曲线
__main__.py   CLI
```

```bash
python -m polidemo_py --selftest                          # 20.2453 Ah / 11150.2 s
python -m polidemo_py --cycles 400 --delta-n 50           # 400 循环 + 跳跃 + 导出
python -m polidemo_py --cycles 20 --no-jump               # 关跳跃（对拍用）
```

400 循环实测：**75 次真实求解 + 24 次跳跃，83 s**，末端 SOH 0.9735、LLI 2.10%、
LAM_p 2.19%、LAM_n 0.01%、膨胀 0.1349 mm，输出见 `output/`。

### 与参考实现的对拍（`_re/compare.py`，118 项）

| 项目 | 最大绝对误差 |
|---|---|
| 106 个参数默认值、9 个派生属性、18 个退化状态字段 | 0 |
| OCP（401 点 × 5 倍率）、Ω 两条路径 | 0 |
| 应力 / SIF / Paris 律 / 可逆厚度 | 0 |
| SEI/CEI/析锂通量、LAM、LLI、不可逆膨胀 | 0 |
| 单循环容量 | 4.7e-13 Ah |
| 单循环总时长 | 5.9e-10 s |
| 单循环不可逆膨胀 | 1.2e-18 mm |
| 步内 V / I / c(r) 剖面 | ≤ 2e-9（相对 1e-12） |
| 200 循环 SOH / LLI / 膨胀（含跳跃） | ≤ 2.4e-10 |
| 跳跃序列 | 完全一致 |

唯一超过 1e-6 的是周向应力 `σ_c,n`（5e-6 Pa，相对 5e-11），来自浓度剖面 1e-9 级差异经样条/多项式拟合放大，物理上可忽略。

### 设计取舍

- **求解器**：直接用 `scipy.integrate.solve_ivp(method="BDF", rtol=1e-10, atol=1e-12, dense_output=True)`，
  与 MATLAB `ode15s` 同类（变阶 BDF）；事件用 `terminal/direction` 复刻原码的四个停机条件。
- **恒压步**：外层对 `jn_tot` 用 `brentq`（先区间倍增找变号点，`xtol=1e-24`）——这是参考实现的做法，不是 MATLAB 原码的 DAE 解法，
  但在该模型上等价（电压对 `jn_tot` 单调）。
- **副反应分配**：定常迭代 16 次（不是牛顿），失败不抛异常，与原码一致。
- **循环跳跃**：6 个容差分量、`cycle_count>50` 才启用、取整下限 5、一阶外推，三种模式（adaptive/capped/fixed）。

### 还没做的

- **GUI**：参考 exe 的 PySide6 界面没有复刻，现在是 CLI + 绘图；需要的话可以按 `param_editor / plots / worker` 的结构另接一层。
- **热模型**：原本就是 beta 且未实现，`enable_temperature=True` 直接抛异常。
- **参数标定**：论文用容量 + 膨胀 + LLI + LAM 联合最小二乘估参（Supplementary S2），这部分不在 exe 里，也没复刻。


# PyBaMM 环境与电池仿真讨论笔记

## 1. 本机装了什么

| 项 | 值 |
|---|---|
| Python | 3.13.9（venv 在 `.venv\`，**不是** base conda） |
| PyBaMM | 26.10.1.1（PyPI 最新） |
| 求解器 | IDAKLU（CasADi 已被官方标记 deprecated） |
| 2D 网格 | scikit-fem 12.0.2（**可选依赖，2D 必须装**，否则 `ModuleNotFoundError: skfem`） |
| 其他 | CasADi 3.8.1、SciPy 1.18.1、matplotlib 3.11.2、pyinstaller（供 POLIDEMO loader 用） |

激活：`.\.venv\Scripts\Activate.ps1`，或直接用 `.\.venv\Scripts\python.exe xxx.py`。
依赖锁在 `requirements.txt`。

> 坑：PyBaMM 默认**不装 matplotlib**，要画图得另装（已装）。
> `sol["Cell temperature [C]"]` 带 60 个贯穿厚度节点，取标量要用
> `X-averaged cell temperature [C]`。

## 2. 实测：模型层级与代价（本机，Chen2020，2C 放电，lumped thermal）

| 模型 | 用时 | 步数 | 末端电压 | 末端温度 | 到 2.5 V 时间 |
|---|---|---|---|---|---|
| SPM | 0.07 s | 64 | 2.500 V | 45.1 °C | 1742 s |
| SPMe | 0.18 s | 104 | 2.500 V | 64.9 °C | 1727 s |
| DFN | 0.34 s | 162 | 2.500 V | 69.4 °C | 1720 s |

结论（比"SPM 快但糙"更具体）：

- 电压曲线 SPM 全程高 100–200 mV，SPMe 与 DFN 几乎重合（见 `model_compare_2C.png`）。
  2C 这种倍率下 SPM 已经不能用。
- 温度差异远大于电压差异：SPM 45 °C vs DFN 69 °C。SPM 没有电解液欧姆热和浓差热源，
  热失控/热管理场景直接排除 SPM。
- 本机 DFN 单次 0.34 s，做参数扫点完全够用。**先不要为了省时间降到 SPM，先降倍率或缩短工况。**

## 3. 循环老化：`pybamm_aging.py`

```
.\.venv\Scripts\python.exe pybamm_aging.py 30
```

SPMe + `ec reaction limited` SEI + `swelling only` + `stress-driven` LAM，参数 OKane2022（5 Ah）。
30 循环 2.0 s（0.07 s/循环），输出 SOH / ΔLLI / LAM 与 `pybamm_aging.png`。

**结果与解读**：30 循环 SOH=0.895，ΔLLI=+10.1%，LAM 仅 0.014%。
即这批参数下老化**完全由 SEI 主导**，LAM 可以忽略。

但这组数**偏激进**（每循环 ~1.7% 容量损失，真实 1C 循环通常百循环才这个量级）。
原因不是模型错，而是参数标定对象不同：OKane2022 原本是给特定电芯/工况拟合的，
拿到 1C 全深度循环 + 5 Ah 假设上必然偏快。**换参数前不要拿这条曲线当寿命预测。**

## 4. 两个真实坑（我都踩过）

### 4.1 `swelling and cracking` + OKane2022 会把求解器搞崩

| 配置 | 30 循环结果 |
|---|---|
| plain / +SEI / +SEI+cracks | 30/30 通过 |
| **+particle mechanics=`swelling and cracking`** | **1/30 就崩**（hmin 塌缩，IDAGetDky） |
| +all（四种一起开） | 11/30 崩 |

缓解手段全试了：`rtol=1e-6/atol=1e-8`、`options={"dt_max":10}`、`newton_mode="casadi"`
——**都没用**。`swelling only` 稳定 30/30。

所以脚本里用 `swelling only`，放弃裂纹显式演化。要裂纹就得换参数集或换模型
（见 §7.3，这正是 POLIDEMO 的强项）。

### 4.2 `sol.cycles` 是 **step 序列**，不是循环序列

`Experiment` 里每个 step（放电/静置/充电/静置）各产生一个 sub_solution，
所以 30 循环会有 **120** 个。按位置取 `sol.cycles[i]` 会拿到交替的放电/充电步。
脚本用 `"2.5 [V]" in str(step.termination)` 筛放电步，比按位置猜稳健。

另外 OKane2022 自带一段**初始 SEI 平台**（约 0.16 A·h，t=0 就存在，不随循环增长）。
它是参数标定基线不是循环损伤，所以 LLI 必须报**相对第 1 循环的增量**，绝对值另记。

## 5. 二维 / 三维能力边界（实测）

### 5.1 到底支持到几维

| 配置 | 是否可用 | 说明 |
|---|---|---|
| `dim=1` + `potential pair` | ✅ | 集流体 1D（沿宽度方向） |
| `dim=2` + `potential pair` | ✅ | **集流体 y-z 面内 2D**（`PotentialPair2plus1D`），厚度方向 x 仍 1D |
| `dim=3` + `potential pair` | ❌ | 选项里声明了 3，但**没有 `PotentialPair3plus1D` 子模型**，会 `UnboundLocalError` |
| `thermal="x-full"` | ✅ 仅 `dim=0` | 厚度方向温度全程解析，不含面内 |
| `thermal="x-lumped"` | ✅ 仅 pouch + `dim<=2` | **厚度方向集中 + 面内 2D 导热**（`CurrentCollector2D`） |
| `Basic3DThermalSPM` | ⚠️ | 存在，但**只算热不算电化学**，且要自己给 `cell` 域建网格 |

结论：**PyBaMM 26.10 没有真正的 3D 电化学**。它有的是
"面内 2D 集流体电位 + 厚度 1D 电化学 + 厚度方向集中/解析的热"，
文献里常写成 **2+1D / 3+1D（伪三维）**。真 3D 电化学目前只能 COMSOL 那类工具。

对"电芯内部热或力的不均匀"这个需求，`dim=2 + thermal="x-lumped"` 正好是主力配置：
面内电流分布、面内温度分布都能出，且比真 3D 便宜一个量级以上。

### 5.2 实测：不均匀性到底吃多少容量

脚本 `pouch2d.py`，Marquis2019（0.68 Ah，约 3.0×5.5 cm），放电到参数自带 3.1 V 截止：

| 倍率 | 1D cap | 2D cap | 容量差 | 面内温差 | 电流不均匀度 | 用时 |
|---|---|---|---|---|---|---|
| 1C | 0.6898 | 0.6898 | **+0.003%** | 0.01 K | 1.2% | 6.9 s |
| 2C | 0.6818 | 0.6818 | **+0.005%** | 0.02 K | 3.0% | 7.0 s |
| 3C | 0.6752 | 0.6753 | **+0.012%** | 0.04 K | 5.0% | 7.1 s |

再把集流体电导降到 1/16（模拟接触不良 / 薄集流体）：

| 电导倍率 | 容量 | 面内温差 | 不均匀度 |
|---|---|---|---|
| x1 | 0.6753 | 0.04 K | 5.0% |
| x0.25 | 0.6757 | 0.12 K | 19.1% |
| x0.0625 | 0.6771 | 0.51 K | 59.2% |

大电芯（NCA_Kim2011，0.14×0.2 m）3C：1D=0.4147 / 2D=0.4149（**+0.061%**），面内温差 0.91 K。

**三条结论**：

1. **正常工况下，面内不均匀对总容量的影响小到可以忽略**（<0.01%）。
   直接用 1D 算容量和放电曲线是**安全的**，不必上 2D。
2. **但温度不均匀大一个量级**：3C 大电芯已经 0.9 K，电导降到 1/16 时 0.5 K。
   热管理、析锂风险、局部老化这些场景必须看面内分布——这就是上 2D 的真正理由。
3. **容量差是正的**（2D 略大于 1D）。原因是极耳附近先耗尽、远离极耳的活性材料被更充分
   利用，净效果略微提高截止前的总放电量。0.01% 量级，别过度解读，
   但它说明"不均匀一定让容量变差"这个直觉并不总对。

### 5.3 什么时候该上 2D，什么时候别碰

| 你想回答的问题 | 用什么 |
|---|---|
| 放电曲线、容量、整体 SOH | **1D**（SPMe/DFN），快、够用 |
| 面内温度分布、热点位置 | 2D + `x-lumped`（约 7 s/次） |
| 极耳设计、集流体厚度、接触电阻 | 2D `potential pair`（正是它擅长） |
| 局部析锂 / 局部 LAM 的空间分布 | 2D，但要开对应退化选项，注意 §4.1 的稳定性坑 |
| 真 3D 电流路径（圆柱卷绕、复合集流体） | ❌ PyBaMM 做不了，上 COMSOL |

### 5.4 快速仿真的省法

- **降维先降到 1D**，只在需要空间分布时才开 2D。
- 2D 单次约 7 s（SPMe），扫 20 组参数约 2 分钟——**完全够用，不需要简化模型**。
- 面内网格默认 10×10 就够；要解析极耳细节再加密，代价近似线性。
- 想更便宜：`dim=2` 配 SPM（不是 SPMe），但注意 §2 里 SPM 高倍率电压偏高的坑。

## 6. 区域化 LLI / LAM 与 dQ/dV（`dqdv_regions.py`）

### 6.1 结论先说：2D 做不了空间退化

实测 PyBaMM 26.10.1.1：

| 尝试 | 结果 |
|---|---|
| 2D 里设**普通参数**空间分布（如 eps 沿 y） | ✅ 可以，函数收到符号 `(x,y,z)`，随离散化生效 |
| 2D 里设**退化参数**（LAM 项）空间分布 | ✅ 参数赋值不报错 |
| **2D + `loss of active material` 一起求解** | ❌ `ModelError: rhs and initial conditions must have the same shape` |
| 同上，`dim=1` | ❌ 同样报错 |
| 同上，纯 1D 电极（`dim=0`） | ✅ 正常 |

根因：LAM 子模型是按 **1D 电极**写的，一旦接上集流体维度就形状不匹配。
所以 **"2D 里给不同区域不同 LAM/LLI"这条路走不通**，这是 PyBaMM 当前的硬限制，
不是配置问题（我试了 dim=0/1/2、换参数集、换求解器）。

### 6.2 替代方案：N 个并联区域

把电芯切成 N 个并联区域，每区独立 LLI/LAM，共用端电压：

    Q_tot(V) = Σ Q_i(V)        dQ/dV = Σ dQ/dV|_i

每个区域由 PyBaMM 实算的准静态曲线（C/20 慢放，保证 dQ/dV 平滑）缩放得到。
脚本 `dqdv_regions.py`，毫秒级。

**实测结果**（Marquis2019，健康 Q0=0.70 Ah）：

| 场景 | 容量 | dQ/dV 峰高 | 峰位 |
|---|---|---|---|
| 健康 | 0.7016 Ah | 26.97 | 3.684 V |
| 均匀退化 15% | 0.5964 Ah | 25.50 | 3.709 V |
| **分区退化 0/10/20/30%** | 0.5964 Ah | **8.31** | 3.733 V |
| 混合分区 | 0.5841 Ah | 14.87 | 3.723 V |

**关键发现**：同样 15% 的平均容量损失，**分区退化让 dQ/dV 峰高从 27 掉到 8.3（−69%）**，
而且峰被拆成多个小峰/波浪。这是非均匀老化在 dQ/dV 上最可靠的指纹：
**峰形被抹平，而不是整体等比下降**。只测总容量根本看不出差别（两者都是 0.5964 Ah）。

### 6.3 一个重要局限（别被误导）

本降阶模型里 **LLI 与 LAM 结果完全相同**（峰 25.498 / 3.709 V 一模一样）。
这不是 bug，是模型的固有局限：只靠全电池 dQ/dV 的容量缩放，**无法区分**容量损失来自
锂库存还是活性材料。要分开必须靠电极级信息（半电池 dQ/dV、参比电极、拆解）。

所以这个模型能回答"非均匀性怎么改变 dQ/dV 峰形"，**不能**回答
"这 15% 损失里 LLI/LAM 各占多少"。

### 6.4 实操建议

- 想看**非均匀性对 dQ/dV 的影响** → 用本脚本的分区叠加，完全够用且快。
- 想要**真实电极级 LLI/LAM 分解** → 必须上 POLIDEMO（它有电极级状态），
  或做半电池实验。
- 若一定要在 PyBaMM 里做空间退化 → 目前只能**手动分块**（每块一个独立 1D 仿真，
  自己并联），或者等官方修 LAM 子模型的维度支持。



## 7. 和 POLIDEMO 的关系

你手上已有 POLIDEMO 复现：22 Ah LCO/石墨软包，P2D + 力学，含 SEI / 裂纹 / LAM /
膨胀 / SOC 控制，带 **cycle-jumping（ΔN 自适应跳跃）** 做长寿命加速。

已验证：`.\.venv\Scripts\python.exe polidemo_from_exe.py` 自检通过
（20.2453 Ah / 0.000343 mm / 11150.2 s，与官方一致）。

### 7.1 能力对照

| 能力 | PyBaMM | POLIDEMO |
|---|---|---|
| 模型层级 | SPM/SPMe/DFN/MPM/MSMR，任意组合 | 固定 P2D |
| 参数集 | 19 套内置（Prada2013 就是 Poli 组的数据） | 自带 22 Ah 实测标定 |
| 力学 | 颗粒尺度膨胀；裂纹模式**不稳定** | **核心**：裂纹+力学+膨胀耦合 |
| LAM | 有（stress-driven） | 有 |
| 长寿命 | 逐循环，O(N) | **cycle-jumping，可跳数百循环** |
| 速度 | 0.07 s/循环 | 需实测（demo 800 循环 capped 跳跃） |
| 电芯尺度 | **2D 弱形式更成熟**（见 §5） | 软包叠层几何，无面内 2D |

### 7.2 建议的分工（不是二选一）

1. **PyBaMM 做快速前哨**：参数敏感性、倍率/温度扫描、DFN vs SPMe 收敛性判断。
   单点亚秒级，可以在 POLIDEMO 跑一次的时间里扫几十组。
2. **PyBaMM 给 POLIDEMO 当参照系**：同一工况（1C 全深度）下两边都跑前 N 循环，
   比对电压曲线和 LLI/LAM 分解。差异若出现在**前几循环**，是参数问题；
   若在**几十循环后**发散，是 cycle-jumping 的跳跃误差——这是你复现工作最该盯的点。
3. **POLIDEMO 做长寿命主力**：裂纹/LAM/膨胀这些 PyBaMM 在 OKane2022 下算不稳的，
   POLIDEMO 才是主场。PyBaMM 的结论（哪个通道主导）可以用来给 ΔN 选上限。
4. **面内不均匀归 PyBaMM**：POLIDEMO 是叠层 1D，做不了极耳/集流体面内分布；
   这一块用 PyBaMM 的 2D 补。注意 §5.2：不均匀对**容量**影响可忽略，
   但对**温度**影响明显，所以别指望 2D 能解释容量衰减。

### 7.3 值得做的一件事：校验 cycle-jumping 的误差

cycle-jumping 是 POLIDEMO 的核心卖点，也是最大风险点。目前 `demo_lifetime.py` 只跑了
capped 跳跃，**没量化跳跃本身引入的偏差**。可以用 PyBaMM 做基准：

- 取 1–50 循环，PyBaMM 逐循环跑（约 4 s）；POLIDEMO 用 ΔN=1、5、10 跑同一段。
- 对比 50 循环末端的 SOH / LLI / LAM，给出"ΔN=10 相对 ΔN=1 的偏差"。
- 这个数直接决定 `tol_jump_size=0.0008` 该不该调。

这是两边组合唯一能做出**单靠任何一方都得不到**的结论的地方。

## 8. 复现文件

| 文件 | 作用 |
|---|---|
| `requirements.txt` | 完整依赖锁 |
| `pybamm_aging.py` | 循环老化，输出 SOH/LLI/LAM |
| `pybamm_aging.png` | 上述结果图 |
| `pouch2d.py` | 1D vs 2D 软包不均匀性扫描 |
| `pouch2d_compare.png` | 放电曲线 + 不均匀性趋势 |
| `pouch2d_fields.png` | 面内电流/温度分布场图 |
| `dqdv_regions.py` | 区域化 LLI/LAM -> dQ/dV |
| `dqdv_regions.png` | dQ/dV 峰形随分区退化变化 |
| `model_compare_2C.png` | SPM/SPMe/DFN 2C 对比 |
| `polidemo_from_exe.py` | POLIDEMO 加载器（已在本 venv 验证） |

## 9. 下一步可选方向

- **Prada2013**（同属 Poli 组）能不能把 POLIDEMO 的参数搬进 PyBaMM，做真正的同参数对照？
- PyBaMM 的 `Simulation(..., calc_esoh=False)` / 灵敏度（`pybamm.Simulation` + `sensitivities`）
  对 POLIDEMO 的参数辨识有没有用？
- 电芯尺度：`Basic3DThermalSPM` 要自建 `cell` 域网格才跑得起来，值不值得搭？
  或者用 2D `potential pair` + `x-lumped` 就够覆盖软包面内不均？

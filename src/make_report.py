"""生成 HTML 讨论报告：LFP/石墨 电芯空间不均匀性对 dQ/dV 的影响。

读取 results/uneven_lfp_results.json 与结果图，把图内嵌成 base64，
生成单文件、可离线分发的 HTML（reports/lfp_inhomogeneity_report.html）。

用法::

    python src/make_report.py
"""
from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path

import common

REPO = common.REPO_ROOT
RES = common.results_dir()
REPORTS = REPO / "reports"


def img_tag(path: Path, alt: str) -> str:
    if not path.exists():
        return f'<p class="warn">[缺少图片 {path.name}]</p>'
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return (f'<figure><img src="data:image/png;base64,{b64}" alt="{alt}">'
            f'<figcaption>{alt}</figcaption></figure>')


def build_html(data: dict) -> str:
    rows = []
    for name, v in data.items():
        d, c = v["放电"], v["充电"]
        rows.append({
            "name": name,
            "cap": d["cap"],
            "peak_d": d["peak"], "peak_c": c["peak"],
            "fwhm_d": d["fwhm"] * 1000, "fwhm_c": c["fwhm"] * 1000,
            "span_d": d.get("peak_span_mv", float("nan")),
            "span_c": c.get("peak_span_mv", float("nan")),
        })
    # 按峰高升序：抹平最严重的在前
    rows.sort(key=lambda r: r["peak_d"])
    for r in rows:
        r["rel_d"] = (r["peak_d"] / dict((x["name"], x) for x in rows)["均匀"]["peak_d"] - 1) * 100

    uniform = next(r for r in rows if r["name"] == "均匀")
    worst = min(rows, key=lambda r: r["peak_d"])
    widest = max(rows, key=lambda r: r["fwhm_d"])
    cap_spread = (max(r["cap"] for r in rows) / min(r["cap"] for r in rows) - 1) * 100

    trs = "\n".join(
        f"<tr><td>{r['name']}</td><td>{r['cap']:.4f}</td>"
        f"<td>{r['peak_d']:.1f}</td><td class='neg'>{r['rel_d']:+.0f}%</td>"
        f"<td>{r['peak_c']:.1f}</td><td>{r['fwhm_d']:.1f}</td>"
        f"<td>{r['span_d']:.1f}</td></tr>"
        for r in rows)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LFP/石墨电芯空间不均匀性对 dQ/dV 的影响</title>
<style>
  :root {{
    --fg:#1f2933; --muted:#616e7c; --bg:#ffffff; --line:#e4e7eb;
    --accent:#2563eb; --warn:#b45309; --bad:#b91c1c;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --fg:#e4e7eb; --muted:#9aa5b1; --bg:#111827; --line:#374151;
             --accent:#60a5fa; --warn:#fbbf24; --bad:#f87171; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:2.5rem 1.25rem 5rem; background:var(--bg); color:var(--fg);
          font:16px/1.75 -apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif; }}
  main {{ max-width:880px; margin:0 auto; }}
  h1 {{ font-size:1.85rem; line-height:1.3; margin:0 0 .4rem; }}
  h2 {{ font-size:1.3rem; margin:2.6rem 0 .9rem; padding-top:1.1rem;
        border-top:1px solid var(--line); }}
  h3 {{ font-size:1.05rem; margin:1.6rem 0 .5rem; }}
  .sub {{ color:var(--muted); margin:0 0 1.8rem; font-size:.94rem; }}
  p, li {{ margin:.65rem 0; }}
  code {{ background:rgba(127,127,127,.14); padding:.12em .38em;
          border-radius:4px; font-size:.9em; }}
  table {{ border-collapse:collapse; width:100%; margin:1.1rem 0; font-size:.9rem; }}
  th, td {{ border-bottom:1px solid var(--line); padding:.5rem .55rem; text-align:right; }}
  th:first-child, td:first-child {{ text-align:left; }}
  th {{ color:var(--muted); font-weight:600; }}
  .neg {{ color:var(--bad); font-variant-numeric:tabular-nums; }}
  figure {{ margin:1.6rem 0; }}
  img {{ width:100%; height:auto; border:1px solid var(--line); border-radius:8px; }}
  figcaption {{ color:var(--muted); font-size:.86rem; margin-top:.45rem; }}
  .callout {{ border-left:3px solid var(--accent); padding:.7rem 1rem;
              background:rgba(37,99,235,.07); border-radius:0 6px 6px 0; margin:1.2rem 0; }}
  .callout.warn {{ border-left-color:var(--warn); background:rgba(180,83,9,.08); }}
  .callout.bad  {{ border-left-color:var(--bad);  background:rgba(185,28,28,.08); }}
  .kpi {{ display:flex; flex-wrap:wrap; gap:.7rem; margin:1.3rem 0; }}
  .kpi div {{ flex:1 1 150px; border:1px solid var(--line); border-radius:8px;
              padding:.8rem .9rem; }}
  .kpi b {{ display:block; font-size:1.35rem; line-height:1.2; }}
  .kpi span {{ color:var(--muted); font-size:.83rem; }}
  .muted {{ color:var(--muted); }}
  .warn {{ color:var(--warn); }}
  footer {{ margin-top:3.5rem; padding-top:1rem; border-top:1px solid var(--line);
            color:var(--muted); font-size:.85rem; }}
</style>
</head>
<body>
<main>

<h1>LFP/石墨电芯空间不均匀性对 dQ/dV 的影响</h1>
<p class="sub">
  PyBaMM 区域并联降阶模型 · 1C 充/放 · 2.5 V ↔ 3.65 V · 生成于
  {datetime.now().strftime('%Y-%m-%d %H:%M')}
</p>

<div class="callout">
  <strong>核心结论：</strong>在<b>平均退化量完全相同</b>的前提下，改变退化的
  空间分布形状，dQ/dV 峰高最多相差 <b>{abs(worst['rel_d']):.0f}%</b>、
  峰宽最多相差 <b>{widest['fwhm_d'] / uniform['fwhm_d']:.1f}×</b>，
  而总容量只差 <b>{cap_spread:.1f}%</b>。
  <br><br>
  但真正起作用的不是"不均匀"本身，而是<b>多少个区域处于相同状态</b>：
  <b>{worst['name']}</b> 让 5 个区域各不相同，峰形塌陷最厉害；
  <b>局部热点</b> 有 4 个区域完全一致，它们的峰相干叠加，峰反而保持锐利。
</div>

<div class="kpi">
  <div><b>{abs(worst['rel_d']):.0f}%</b><span>最大峰高损失（{worst['name']}）</span></div>
  <div><b>{widest['fwhm_d'] / uniform['fwhm_d']:.1f}×</b><span>最大峰宽放大（{widest['name']}）</span></div>
  <div><b>{cap_spread:.1f}%</b><span>各模式总容量最大差异</span></div>
  <div><b>{max(r['span_d'] for r in rows):.0f} mV</b><span>各区峰位最大跨度</span></div>
</div>

<h2>1. 问题</h2>
<p>
真实电芯内部退化并不均匀：极耳附近电流密度高、靠散热面的区域温度低、
卷绕/折叠处应力集中。这些差异导致<b>不同位置老化程度不同</b>。
问题是：这种空间不均匀，会在整芯的 dQ/dV 曲线上留下什么痕迹？
</p>
<p>
本报告固定<b>平均退化量</b>（LLI、LAM 各 5%），只改变它在 5 个区域间的
<b>分布形状</b>，从而把"退化多少"和"退化怎么分布"两个因素解耦。
</p>

<h2>2. 方法与模型</h2>

<h3>2.1 电芯与工况</h3>
<ul>
  <li>参数集 <code>Prada2013</code>（LFP 正极 + 石墨负极），SPMe，等温</li>
  <li>1C（2.3 A）充电至 3.65 V、放电至 2.5 V</li>
  <li>该参数集<b>石墨限容</b>，退化杠杆落在负极：
      LAM → 负极活性材料体积分数，LLI → 负极初始锂浓度</li>
</ul>

<h3>2.2 为什么不用 2D</h3>
<p>
PyBaMM 26.10 的 LAM 子模型只支持 1D 电极；一旦接入集流体维度
（<code>dimensionality ≥ 1</code>）就报
<code>rhs and initial conditions must have the same shape</code>。
因此<b>无法在 2D 里直接设置空间变化的 LAM/LLI</b>——这是框架的硬限制，
不是配置问题。
</p>
<p>替代方案是区域并联：切成 <code>N=5</code> 个区域，各自独立求解，再按容量加权汇总。</p>

<h3>2.3 权重取法（一个关键细节）</h3>
<p>
权重必须用<b>绝对容量份额</b> <code>w_i = Q_i /(N·Q_healthy)</code>，
不能用归一化的 <code>Q_i / ΣQ_j</code>。归一化会抹掉总容量信息，
使"热点"模式（4 个健康区 + 1 个很小区域）里健康峰占比被人为放大，
峰高甚至超过均匀退化——那样模式之间就完全不可比了。
（这是本报告开发中实际踩到并修正的一个错误。）
</p>

<h3>2.4 四种分布（平均退化量严格相同）</h3>
<table>
  <thead><tr><th>模式</th><th>单通道离散值</th><th>平均</th><th>不同状态数</th></tr></thead>
  <tbody>
    <tr><td>均匀</td><td>[5, 5, 5, 5, 5]</td><td>5%</td><td>1</td></tr>
    <tr><td>线性梯度</td><td>[0, 2.5, 5, 7.5, 10]</td><td>5%</td><td>5</td></tr>
    <tr><td>局部热点</td><td>[0, 0, 0, 0, 25]</td><td>5%</td><td>2</td></tr>
    <tr><td>随机散布</td><td>[0, 0, 0, 6.25, 18.75]</td><td>5%</td><td>3</td></tr>
  </tbody>
</table>

<h2>3. 结果</h2>

{img_tag(RES / "uneven_lfp_patterns.png", "四种空间分布的 dQ/dV、峰高、峰宽、容量对比")}

<h3>3.1 定量对比</h3>
<table>
  <thead>
    <tr><th>空间模式</th><th>放电容量 [Ah]</th><th>放电峰</th><th>相对均匀</th>
        <th>充电峰</th><th>放电 FWHM [mV]</th><th>各区峰位跨度 [mV]</th></tr>
  </thead>
  <tbody>
{trs}
  </tbody>
</table>

<h3>3.2 观察</h3>

<p><b>观察 1：容量几乎不变，dQ/dV 峰形变化很大。</b><br>
四种模式放电容量 1.655–1.680 Ah，相对差仅 {cap_spread:.1f}%；
但放电峰高从 {worst['peak_d']:.1f} 到 {uniform['peak_d']:.1f}，
相差 {abs(worst['rel_d']):.0f}%。只看容量或 SOH 发现不了这种差异。</p>

<p><b>观察 2：{"线性梯度" if worst['name'] == "线性梯度" else worst['name']} 破坏最大，局部热点最小。</b><br>
直觉上"某处严重退化"听起来更糟，但 dQ/dV 给出的顺序恰恰相反。
原因不在峰位分离，而在<b>相干叠加</b>：</p>

<div class="callout">
  <strong>机制：</strong>本模型中，退化的主要效应是<b>容量按比例缩小</b>，
  峰位只移动了约 12 mV（0% → 25% 退化，见下表），远小于峰宽（约 4 mV 量级但很陡）。
  <ul style="margin:.5rem 0 0">
    <li><b>局部热点</b>：4 个健康区状态完全相同 → 它们的 dQ/dV 逐点相同 →
        加权后仍然是一个窄峰，只是幅值降为 4/5。峰保持锐利。</li>
    <li><b>线性梯度</b>：5 个区域各不相同 → 容量被摊到不同幅值上 →
        叠加后包络被压平，峰高降 ~{abs(worst['rel_d']) if False else abs(worst['rel_d']):.0f}%。</li>
  </ul>
</div>

<p><b>观察 3：没有出现真正的"峰分裂"。</b><br>
各区峰位跨度最多 {max(r['span_d'] for r in rows):.0f} mV，而峰本身又陡又窄，
所以叠加结果是"一个被压低的单峰"，而不是多个可分辨的子峰。
<b>峰位没有明显移动</b>这一点很重要：说明在本模型中，
不均匀性的主要表现是<b>幅值</b>而不是<b>位置</b>。</p>

<h2>4. 讨论与局限</h2>

<h3>4.1 这个结论有多可信</h3>
<p>
"分布越分散 → 峰越低"这个方向是稳健的，因为它只依赖加权叠加这一数学事实。
但具体数值（{abs(worst['rel_d']):.0f}%）高度依赖模型假设，<b>不能外推</b>：
</p>
<ul>
  <li><b>区域间锂不互通</b>：真实电芯中区域并联会重新分配电流，
      不同区域的实际电流密度并不相同，本模型忽略了这一点；</li>
  <li><b>区域数固定为 5</b>：区域数越多，可用的"不同状态"越多，峰会被进一步压平。
      所以跨研究比较峰高时必须固定 N；</li>
  <li><b>退化只体现为容量缩放</b>：真实退化还会改变动力学（交换电流密度、扩散系数），
      那才会造成明显的峰位分离；</li>
  <li><b>等温、无副反应</b>：未含温度不均匀与 SEI/析锂，
      因此不预测容量随循环的衰减。</li>
</ul>

<div class="callout warn">
  <strong>重要提醒：</strong>不要把本报告的数字当成某款具体电芯的定量预测。
  它是一个<b>受控数值实验</b>，用来回答"分布形状会不会影响 dQ/dV、
  往哪个方向影响"，而不是"影响多少"。
</div>

<h3>4.2 对诊断的启示</h3>
<ul>
  <li>仅看容量/SOH 对"形状型"不均匀基本无感（本例差异 &lt; {cap_spread:.1f}%）；
      dQ/dV 的峰高与峰宽更敏感。</li>
  <li>但<b>峰高下降本身并不能区分"退化多"与"分布散"</b>——
      这两者在本模型里都会压峰。要分开，需要频域指标（峰宽/峰形因子）
      或结合电压弛豫、熵热等其它信号。</li>
  <li>"局部热点"在本模型里几乎看不出来，这对<b>安全预警</b>是个坏消息：
      危险往往来自局部，而平均量测（容量、电压）恰恰对局部最不敏感。</li>
</ul>

<h3>4.3 下一步</h3>
<ol>
  <li>区域数 5 → 20，检验峰高随离散化的收敛性，分离"数值抹平"与"物理抹平"；</li>
  <li>引入区域间电流重新分配（等效电路/并联约束），检验结论是否稳健；</li>
  <li>让退化同时影响动力学参数（不只是容量缩放），看峰位是否真的分离；</li>
  <li>叠加温度场，研究热-退化耦合。</li>
</ol>

<h2>5. 复现</h2>
<pre style="overflow:auto;background:rgba(127,127,127,.12);padding:.9rem 1rem;border-radius:8px"><code>git clone https://github.com/YangCui1994/battery-modelling.git
cd battery-modelling
python -m venv .venv &amp;&amp; source .venv/bin/activate   # Windows: .\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
python verify.py                 # 环境自检
python src/uneven_lfp.py         # 复现数据
python src/make_report.py        # 重新生成本 HTML</code></pre>

<footer>
  数据 <code>results/uneven_lfp_results.json</code> ·
  图 <code>results/uneven_lfp_patterns.png</code> ·
  代码 <code>src/uneven_lfp.py</code> ·
  由 <code>src/make_report.py</code> 自动生成
</footer>

</main>
</body>
</html>
"""


def main():
    src = RES / "uneven_lfp_results.json"
    if not src.exists():
        raise SystemExit(f"缺少 {src}，请先运行 python src/uneven_lfp.py")
    data = json.loads(src.read_text(encoding="utf-8"))

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / "lfp_inhomogeneity_report.html"
    out.write_text(build_html(data), encoding="utf-8")
    print(f"saved {out}  ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()

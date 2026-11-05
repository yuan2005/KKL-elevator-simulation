# 双组电梯调度模拟

## 模型与结果

六部电梯分为两组各三部，独立调度。乘客同时按两组按钮，候梯乘客只有一份；
一组接走乘客后不会通知另一组，另一组呼梯保留到本组到站开门。
实验比较不同人流下的候选策略，再通过同状态分支和独立全天模拟验证切换收益。

- [最终中文报告](results/dual_bank_v2_2026-11-04/report_zh.md)
- [条件排名](results/dual_bank_v2_2026-11-04/conditional_rankings.csv)
- [完整统计结果](results/dual_bank_v2_2026-11-04/statistics.json)
- [审计摘要](results/dual_bank_v2_2026-11-04/audit.json)

5880条正式轨迹均完成。直接课表切换相对各档固定参照，主/细步长平均等待差
为+0.463/+0.132秒，同时95%区间均跨零，没有验证出可靠收益。
候选排名不代表绝对最优；本实验未找到满足证据门槛的可靠切换规则。

## 文件结构

- `config.py`：建筑、车辆与服务参数。
- `building.py`、`sim.py`：车辆状态和基础运行逻辑。
- `traffic.py`：随机乘客生成。
- `research/dual_bank/engine.py`、`physics.py`、`policies.py`：双组模型、运动学与控制。
- `research/dual_bank/timetable.py`、`demand.py`：全天日程与持续人流。
- `research/dual_bank/experiment.py`：开发选优、同状态分支与独立验证。
- `research/dual_bank/analysis.py`、`audit.py`：统计报告和原始记录重建。
- `research/dual_bank/test_*.py`：模型及实验过程测试。
- `results/dual_bank_v2_2026-11-04/raw/`：最终实验原始轨迹，仅保留本地，不包含在GitHub仓库中。

## 运行

Python 3.12，依赖：

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s research\dual_bank -p "test_*.py" -v
```

完整复现须使用新目录，不能覆盖已发布的结果。计算量较大，最多使用四个工作进程：

```powershell
python -m research.dual_bank.experiment freeze --root results\reproduction
python -m research.dual_bank.experiment develop --root results\reproduction --workers 4
python -m research.dual_bank.experiment train --root results\reproduction --workers 4
python -m research.dual_bank.experiment conditions --root results\reproduction --workers 4
python -m research.dual_bank.experiment transitions --root results\reproduction --workers 4
python -m research.dual_bank.experiment daily --root results\reproduction --workers 4
python -m research.dual_bank.analysis --root results\reproduction
python -m research.dual_bank.audit --root results\reproduction
```

GitHub版本包含模型、实验代码、最终中文报告、统计表、冻结协议与校验摘要，
不包含约2.13 GiB的5880条原始轨迹。模型测试不依赖这些轨迹；
已有结果的报告重算和完整审计需要本地原始轨迹，不能仅凭GitHub下载文件执行。
本地原始轨迹齐全时，统计报告可由`python -m research.dual_bank.analysis`重新生成；
`python -m research.dual_bank.audit`会重建原始记录并执行选定回放。
原运行参数与源码摘要记录在`protocol.json`，精简代码的校验记录在`source_manifest.json`。
原始轨迹已移除执行机器的绝对路径；乘客输入、服务、决策和统计数值不变。

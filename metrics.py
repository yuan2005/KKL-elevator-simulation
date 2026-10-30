"""评价指标。"""
import numpy as np
from scipy import stats

import config as C


def summarize(sim, t_from=0.0, t_to=None, keep=None):
    """统计到达时刻在 [t_from, t_to) 内（且满足 keep(p) 的）乘客。
    没被服务的乘客按"等到仿真结束"计入（删失）。"""
    t_to = sim.gen_end if t_to is None else t_to
    ps = [p for p in sim.pax if t_from <= p.t < t_to and (keep is None or keep(p))]
    if not ps:
        return dict(n=0, awt=0.0, p90=0.0, lwt=0.0, att=0.0, unserved=0,
                    distance_km=0.0, starts=0, car_hours=0.0)
    end = sim.t
    waits = np.array([(p.t_board if p.t_board is not None else end) - p.t for p in ps])
    journeys = np.array([p.t_alight - p.t for p in ps if p.t_alight is not None] or [0.0])
    return dict(
        n=len(ps),
        awt=float(waits.mean()),
        p90=float(np.percentile(waits, 90)),
        lwt=float((waits > C.LONG_WAIT).mean() * 100),
        att=float(journeys.mean()),
        unserved=int(sum(p.t_board is None for p in ps)),
        distance_km=sum(c.distance for c in sim.cars) / 1000,
        starts=sum(c.starts for c in sim.cars),
        car_hours=sum(c.active_time for c in sim.cars) / 3600,
    )


def mean_ci(x, level=0.95):
    x = np.asarray(x, dtype=float)
    m = x.mean()
    if len(x) < 2:
        return m, 0.0
    h = stats.t.ppf((1 + level) / 2, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x))
    return m, h


def paired_test(a, b):
    """同一组随机种子下 a、b 两个组合的配对 t 检验。返回 (平均差 a-b, p 值)。"""
    a, b = np.asarray(a), np.asarray(b)
    d = a - b
    if np.allclose(d, 0):
        return 0.0, 1.0
    return float(d.mean()), float(stats.ttest_rel(a, b).pvalue)

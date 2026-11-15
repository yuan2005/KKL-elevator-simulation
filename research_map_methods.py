"""Development-only nested CV of strategy-map training objectives; no simulations."""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import KFold, StratifiedKFold
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor

import experiments as E
from complete_experiments import OUTPUT as INPUT, ROOT, digest, load, save, sha
from controllers import FEATURES


OUTPUT = ROOT / "results" / "two_independent_banks" / "map_method_comparison_v2"
FAMILIES = ("legacy_label", "mean_winner", "cost_tree", "relative_cost_tree")
CONFIGS = tuple((depth, leaf) for depth in range(1, 7) for leaf in (8, 16))
CANDIDATES = tuple(E.SWITCHABLE)


def read_development(root):
    protocol = load(root / "protocol.json")
    if protocol["status"] != "completed" or load(root / "validation.json")["status"] != "passed":
        raise ValueError("Require verified complete source study")
    table = pd.read_csv(root / "e1_points.csv")
    table = table[table.point.str.startswith(("P", "H"))].reset_index(drop=True)
    raw = pd.read_csv(root / "e1_raw.csv")
    if len(table) != 548 or table.point.duplicated().any():
        raise ValueError("Expected 500 original plus48 high-load development points")
    for _, row in table.iterrows():
        group = raw[raw.point == row.point]
        if len(group) != 60 or group.groupby("combo").seed.nunique().to_dict() != {key: 10 for key in E.MAP_COMBOS}:
            raise ValueError("Incomplete six-strategy paired development point")
        if not all(group.groupby("seed").case_id.count() == 6):
            raise ValueError("Unpaired seed groups")
        for key in E.MAP_COMBOS:
            if abs(group[group.combo == key].awt.mean()-row[f"awt_{key}"]) > 1e-9:
                raise ValueError("Development cost table differs from verified raw CSV")
        if row.label not in CANDIDATES or row.best_by_mean not in CANDIDATES:
            raise ValueError("Invalid candidate label")
    values = table[[*FEATURES, *[f"awt_{key}" for key in E.MAP_COMBOS]]].to_numpy()
    if not np.isfinite(values).all() or np.any(table[[f"awt_{key}" for key in E.MAP_COMBOS]].to_numpy() < 0):
        raise ValueError("Invalid features or waiting costs")
    return table


def fit_model(family, config, x, costs, labels):
    depth, leaf = config
    if family in ("legacy_label", "mean_winner"):
        model = DecisionTreeClassifier(max_depth=depth, min_samples_leaf=leaf, random_state=95301)
        targets = labels if family == "legacy_label" else np.array(CANDIDATES)[np.argmin(costs, axis=1)]
    elif family in ("cost_tree", "relative_cost_tree"):
        model = DecisionTreeRegressor(max_depth=depth, min_samples_leaf=leaf, random_state=95301)
        targets = costs if family == "cost_tree" else costs-costs.min(axis=1, keepdims=True)
    else:
        raise ValueError(f"Unknown map family: {family}")
    return model.fit(x, targets)


def predict(model, family, x):
    values = model.predict(x)
    return values if family in ("legacy_label", "mean_winner") else np.array(CANDIDATES)[np.argmin(values, axis=1)]


def loss(costs, predictions):
    indices = {key: index for index, key in enumerate(CANDIDATES)}
    if len(predictions) != len(costs) or any(key not in indices for key in predictions):
        raise ValueError("Predictions must align with eligible costs")
    chosen = costs[np.arange(len(costs)), [indices[key] for key in predictions]]
    return chosen-costs.min(axis=1)


def select_config(family, x, costs, labels, splits):
    rows = []
    for config in CONFIGS:
        losses = []
        for train, test in splits:
            model = fit_model(family, config, x[train], costs[train], labels[train])
            losses.extend(loss(costs[test], predict(model, family, x[test])))
        rows.append(dict(depth=config[0], min_leaf=config[1], regret_s=float(np.mean(losses))))
    minimum = min(row["regret_s"] for row in rows)
    eligible = [row for row in rows if row["regret_s"] <= minimum*1.03+1e-12]
    selected = min(eligible, key=lambda row: (row["depth"], -row["min_leaf"], row["regret_s"]))
    return (selected["depth"], selected["min_leaf"]), rows


def policy_spec(model, family):
    if family in ("legacy_label", "mean_winner"):
        nodes = E.tree_nodes(model)
    else:
        tree = model.tree_
        nodes = []
        for index in range(tree.node_count):
            costs = tree.value[index, :, 0]
            nodes.append(dict(
                f=-1 if tree.children_left[index] == -1 else int(tree.feature[index]),
                thr=float(tree.threshold[index]), l=int(tree.children_left[index]),
                r=int(tree.children_right[index]), label=CANDIDATES[int(np.argmin(costs))],
                predicted_costs={key: float(cost) for key, cost in zip(CANDIDATES, costs)},
                n=int(tree.n_node_samples[index])))
    return dict(kind="tree", features=FEATURES, nodes=nodes, family=family,
                candidates=CANDIDATES, cost_scale="excess_above_point_minimum" if family == "relative_cost_tree" else "AWT_seconds")


def rule_text(spec):
    result = []
    def walk(index, conditions):
        node = spec["nodes"][index]
        if node["f"] < 0:
            result.append(" AND ".join(conditions or ["all"]) + f" -> {node['label']} (n={node['n']})")
            return
        name = FEATURES[node["f"]]
        walk(node["l"], conditions+[f"{name} <= {node['thr']:.6g}"])
        walk(node["r"], conditions+[f"{name} > {node['thr']:.6g}"])
    walk(0, [])
    return "\n".join(result)+"\n"


def run(output=OUTPUT):
    table = read_development(INPUT)
    sources = {name: sha(ROOT / name) for name in ("research_map_methods.py", "experiments.py", "complete_experiments.py")}
    data_hashes = {name: sha(INPUT / name) for name in ("e1_points.csv", "e1_raw.csv", "validation.json")}
    fingerprint = digest(dict(sources=sources, inputs=data_hashes, families=FAMILIES, configs=CONFIGS))
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError("Use a new output directory; do not overwrite a prior method study")
    protocol = dict(
        status="registered", fingerprint=fingerprint, sources=sources, input_hashes=data_hashes,
        families=FAMILIES, configs=CONFIGS, candidates=CANDIDATES, reference_fixed=E.MAP_COMBOS,
        outer="5fold stratified by original/high-load design; shuffle95311",
        inner="4fold shuffle95321+outer fold; config selection by pooled point regret, simplest within3%",
        full_fit="Only after nested comparison; exploratory candidate, no daily adoption",
        no_holdout_results_read=True, no_simulations=True, no_claim_of_independent_confirmation=True,
        objective="Point-equal mean regret relative to measured minimum among C1/C2/C3/C4/C7",
        limitation="Same ten existing seeds; finite Monte Carlo costs; synthetic-point distribution not daily traffic distribution",
        legacy="Refit conservative label family inside each fold; published all-data tree is not an OOF comparator",
        relative_cost="Subtract measured within-point minimum to focus splits on choice differences, not shared congestion")
    save(output / "protocol.json", protocol)
    x = table[FEATURES].to_numpy()
    costs = table[[f"awt_{key}" for key in CANDIDATES]].to_numpy()
    labels = table.label.to_numpy()
    folds = list(StratifiedKFold(5, shuffle=True, random_state=95311).split(
        x, table.point.str.startswith("H").astype(int)))
    oof, selections = [], []
    for fold, (train, test) in enumerate(folds):
        inner = list(KFold(4, shuffle=True, random_state=95321+fold).split(train))
        best_fixed = min(E.MAP_COMBOS, key=lambda key: (table.iloc[train][f"awt_{key}"].mean(), E.MAP_COMBOS.index(key)))
        methods = {}
        for family in FAMILIES:
            config, scores = select_config(family, x[train], costs[train], labels[train], inner)
            model = fit_model(family, config, x[train], costs[train], labels[train])
            methods[family] = predict(model, family, x[test])
            selections.append(dict(fold=fold, family=family, depth=config[0], min_leaf=config[1], scores=scores))
        methods.update({f"fixed_{key}": np.full(len(test), key) for key in E.MAP_COMBOS})
        methods["train_selected_fixed"] = np.full(len(test), best_fixed)
        for method, predictions in methods.items():
            for index, chosen in zip(test, predictions):
                row = table.iloc[index]
                oof.append(dict(point=row.point, fold=fold, method=method, selected=chosen,
                                chosen_awt=float(row[f"awt_{chosen}"]),
                                oracle_awt=float(costs[index].min()),
                                regret=float(row[f"awt_{chosen}"]-costs[index].min())))
        print(f"Nested comparison: outer fold{fold+1}/5 complete", flush=True)
    oof = pd.DataFrame(oof)
    oof.to_csv(output / "oof_predictions.csv", index=False)
    summaries = []
    for subset, mask in (("all548", oof.point.str.startswith(("P", "H"))),
                         ("original500", oof.point.str.startswith("P")),
                         ("high48", oof.point.str.startswith("H"))):
        filtered = oof[mask]
        baseline = filtered[filtered.method == "legacy_label"].set_index("point")
        fixed = filtered[filtered.method == "train_selected_fixed"].set_index("point")
        for method, group in filtered.groupby("method", sort=False):
            group = group.set_index("point")
            deltas = group.chosen_awt-baseline.loc[group.index].chosen_awt
            fixed_delta = group.chosen_awt-fixed.loc[group.index].chosen_awt
            fold_deltas = deltas.groupby(group.fold).mean()
            summaries.append(dict(
                subset=subset, method=method, points=len(group), mean_regret=float(group.regret.mean()),
                mean_awt=float(group.chosen_awt.mean()), delta_vs_legacy=float(deltas.mean()),
                delta_vs_train_fixed=float(fixed_delta.mean()), median_regret=float(group.regret.median()),
                p90_regret=float(group.regret.quantile(.9)), max_regret=float(group.regret.max()),
                improving_folds=int((fold_deltas < 0).sum()),
                fold_delta_min=float(fold_deltas.min()), fold_delta_max=float(fold_deltas.max()),
                labels={str(key): int(value) for key, value in group.selected.value_counts().items()}))
    save(output / "nested_summary.json", dict(status="completed", summaries=summaries, fold_selections=selections))
    pd.DataFrame([{key: value for key, value in row.items() if key != "labels"} for row in summaries]).to_csv(
        output / "nested_summary.csv", index=False)
    full_inner = list(KFold(5, shuffle=True, random_state=95341).split(x))
    candidates = {}
    for family in FAMILIES:
        config, scores = select_config(family, x, costs, labels, full_inner)
        model = fit_model(family, config, x, costs, labels)
        spec = policy_spec(model, family)
        fitted = predict(model, family, x)
        # Verify serialized deployment-style traversal against the fitted estimator.
        from revalidate_switching import FrozenPolicy
        reproduced = [FrozenPolicy(spec).predict(row) for _, row in table.iterrows()]
        if list(fitted) != reproduced:
            raise AssertionError("Serialized policy differs from fitted model")
        save(output / f"{family}_candidate.json", spec)
        (output / f"{family}_rules.txt").write_text(rule_text(spec), encoding="utf-8")
        candidates[family] = dict(depth=config[0], min_leaf=config[1],
                                  leaves=int(model.get_n_leaves()), scores=scores,
                                  labels={str(key): int(value) for key, value in pd.Series(fitted).value_counts().items()})
    save(output / "full_development_candidates.json", candidates)
    # Recompute each held-out cost directly from the immutable source table.
    original = table.set_index("point")
    for method, group in oof.groupby("method"):
        if group.point.duplicated().any() or set(group.point) != set(table.point):
            raise AssertionError("Missing/duplicate held-out predictions")
        for _, row in group.iterrows():
            if abs(row.chosen_awt-original.loc[row.point, f"awt_{row.selected}"]) > 1e-9:
                raise AssertionError("Held-out waiting cost differs")
    if any(sha(ROOT / name) != checksum for name, checksum in sources.items()) or any(
            sha(INPUT / name) != checksum for name, checksum in data_hashes.items()):
        raise AssertionError("Sources or development inputs changed")
    save(output / "validation.json", dict(status="passed", point_costs_recomputed=True,
         complete_oof_coverage=True, serialized_policies_match=True, sources_and_inputs_unchanged=True,
         no_simulations=True, no_daily_holdout_used=True))
    protocol["status"] = "completed"
    save(output / "protocol.json", protocol)
    print(pd.DataFrame([{k: v for k, v in row.items() if k != "labels"} for row in summaries
                        if row["subset"] == "all548"]).to_string(index=False))
    print("Full-data candidate complexity:", candidates.keys())
    print(f"COMPLETED development-only search: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    run(parser.parse_args().output)

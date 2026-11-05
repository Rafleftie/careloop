"""Train and benchmark the CareLoop follow-up risk model.

Predicts whether a referred patient will MISS their referral visit.
Uses only information available BEFORE the appointment. `reschedule_reason`
is excluded on purpose: it is recorded after a miss, so using it would leak
the answer.

Outputs (in model/artifacts/):
  metrics.json         benchmark + fairness numbers
  model.joblib         final fitted pipeline
and a `risk_scores` table in data/careloop.db with out-of-fold scores and the
top-3 contributing factors per patient.
"""
import json
import os
import sqlite3

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict, train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = os.path.join(ROOT, "data", "careloop.db")
OUT = os.path.join(HERE, "artifacts")
os.makedirs(OUT, exist_ok=True)
SEED = 42

NUMERIC = ["age", "distance_km", "income_band", "has_phone_app",
           "prior_missed", "reminders_sent", "reminders_responded", "days_wait"]
CATEGORICAL = ["screening_type"]

LABELS = {
    "distance_km": "Lives far from the clinic",
    "prior_missed": "Missed an earlier visit",
    "reminders_responded": "Replied to reminders",
    "reminders_sent": "Number of reminders sent",
    "days_wait": "Long wait for the appointment slot",
    "income_band": "Lower income band",
    "age": "Age",
    "has_phone_app": "Has the phone app",
}


def load():
    db = sqlite3.connect(DB)
    df = pd.read_sql_query(
        """SELECT r.referral_id, r.patient_id, p.name, p.age, p.ward, p.distance_km,
                  p.income_band, p.has_phone_app, p.screening_type,
                  r.referred_on, r.appointment_on, r.prior_missed, r.reminders_sent,
                  r.reminders_responded, r.attended
           FROM referrals r JOIN patients p USING (patient_id)""", db)
    db.close()
    df["days_wait"] = (pd.to_datetime(df.appointment_on) - pd.to_datetime(df.referred_on)).dt.days
    df["missed"] = 1 - df["attended"]
    X = pd.get_dummies(df[NUMERIC + CATEGORICAL], columns=CATEGORICAL, dtype=float)
    return df, X, df["missed"].values


def recall_at_top(y, score, frac):
    k = int(round(len(y) * frac))
    idx = np.argsort(-score)[:k]
    return y[idx].sum() / y.sum(), y[idx].mean()


def evaluate(y, p):
    r20, p20 = recall_at_top(y, p, 0.20)
    r30, _ = recall_at_top(y, p, 0.30)
    return {
        "auc": round(roc_auc_score(y, p), 3),
        "avg_precision": round(average_precision_score(y, p), 3),
        "recall_top20": round(r20, 3),
        "precision_top20": round(p20, 3),
        "recall_top30": round(r30, 3),
        "brier": round(brier_score_loss(y, p), 3),
    }


def calibration(y, p, bins=5):
    d = pd.DataFrame({"y": y, "p": p})
    d["bin"] = pd.qcut(d.p, bins, duplicates="drop")
    g = d.groupby("bin", observed=True).agg(predicted=("p", "mean"), actual=("y", "mean"), n=("y", "size"))
    return [{"predicted": round(r.predicted, 3), "actual": round(r.actual, 3), "n": int(r.n)}
            for r in g.itertuples()]


def main():
    df, X, y = load()
    print(f"Referrals: {len(df)}  missed visit rate: {y.mean():.1%}")

    Xtr, Xte, ytr, yte, itr, ite = train_test_split(
        X, y, np.arange(len(y)), test_size=0.30, random_state=SEED, stratify=y)

    base_rate = evaluate(yte, np.full(len(yte), ytr.mean()) + np.random.default_rng(0).normal(0, 1e-6, len(yte)))
    lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
    gb = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200,
                                        random_state=SEED)
    lr.fit(Xtr, ytr)
    gb.fit(Xtr, ytr)

    results = {
        "no_model_random": base_rate,
        "logistic_regression": evaluate(yte, lr.predict_proba(Xte)[:, 1]),
        "gradient_boosting": evaluate(yte, gb.predict_proba(Xte)[:, 1]),
    }
    # random targeting reference: recall in a random 20% is ~20%
    results["no_model_random"] = {"recall_top20": 0.20, "note": "random targeting reference"}

    print("\n=== Benchmark (held-out 30%) ===")
    print(pd.DataFrame(results).T.to_string())

    # Choose final model: prefer the simple, explainable one unless GB is clearly better
    auc_lr, auc_gb = results["logistic_regression"]["auc"], results["gradient_boosting"]["auc"]
    final_name = "gradient_boosting" if auc_gb - auc_lr > 0.02 else "logistic_regression"
    print(f"\nFinal model: {final_name} (AUC LR {auc_lr} vs GB {auc_gb})")

    # Out-of-fold scores for every referral (honest scores for the dashboard)
    cv = StratifiedKFold(5, shuffle=True, random_state=SEED)
    oof = cross_val_predict(lr if final_name == "logistic_regression" else gb,
                            X, y, cv=cv, method="predict_proba")[:, 1]
    oof_eval = evaluate(y, oof)
    print("\nOut-of-fold (5-fold, all referrals):", oof_eval)
    cal = calibration(y, oof)
    print("Calibration (predicted vs actual miss rate):")
    for c in cal:
        print("  ", c)

    # Fairness: does the model find missed visits equally well across groups?
    thr = np.quantile(oof, 0.80)  # flag the top 20%
    flagged = oof >= thr
    groups = {
        "age": pd.cut(df.age, [0, 34, 44, 120], labels=["<35", "35-44", "45+"]),
        "distance": pd.cut(df.distance_km, [0, 5, 10, 100], labels=["<5km", "5-10km", ">10km"]),
        "income_band": df.income_band.map({1: "lowest", 2: "middle", 3: "highest"}),
    }
    fairness = {}
    print("\n=== Fairness (top 20% flagged) ===")
    for gname, g in groups.items():
        rows = []
        for val in pd.Series(g).dropna().unique():
            m = (g == val).values
            yy, pp, ff = y[m], oof[m], flagged[m]
            rows.append({
                "group": str(val), "n": int(m.sum()),
                "actual_miss_rate": round(float(yy.mean()), 3),
                "flag_rate": round(float(ff.mean()), 3),
                "recall_of_missed": round(float(ff[yy == 1].mean()), 3) if yy.sum() else None,
                "auc": round(float(roc_auc_score(yy, pp)), 3) if 0 < yy.sum() < len(yy) else None,
            })
        fairness[gname] = sorted(rows, key=lambda r: r["group"])
        print(f"\n{gname}")
        print(pd.DataFrame(fairness[gname]).to_string(index=False))

    # Final model fitted on all data + top-3 reasons (logistic contributions)
    final = lr if final_name == "logistic_regression" else gb
    final.fit(X, y)
    joblib.dump({"model": final, "columns": list(X.columns), "name": final_name},
                os.path.join(OUT, "model.joblib"))

    # Reasons: contribution = coef * standardized value, relative to the average patient.
    # Always computed from the logistic model so reasons are simple and auditable.
    lr_full = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)).fit(X, y)
    scaler, clf = lr_full[0], lr_full[1]
    Z = scaler.transform(X)
    contrib = Z * clf.coef_[0]          # log-odds contribution vs the average patient
    cols = list(X.columns)
    risky_dir = {c: clf.coef_[0][i] for i, c in enumerate(cols)}

    def reason_text(col, row):
        v = row[col]
        if col == "distance_km":
            return f"Lives {v:.0f} km from the clinic"
        if col == "prior_missed":
            return f"Missed {int(v)} earlier visit(s)"
        if col == "reminders_responded":
            return f"Replied to only {int(v)} of {int(row['reminders_sent'])} reminders"
        if col == "days_wait":
            return f"{int(v)}-day wait for the appointment slot"
        if col == "income_band":
            return "Lower income band (transport cost likely)"
        if col == "age":
            return f"Age {int(v)}"
        return LABELS.get(col, col)

    out_rows = []
    for i in range(len(df)):
        order = np.argsort(-contrib[i])  # biggest risk-raising first
        reasons = []
        for j in order:
            if contrib[i, j] <= 0.05:
                break
            c = cols[j]
            if c in ("reminders_sent", "has_phone_app") or c.startswith("screening_type"):
                continue
            reasons.append({"factor": c, "text": reason_text(c, df.iloc[i]),
                            "points": round(float(contrib[i, j]) * 10, 1)})
            if len(reasons) == 3:
                break
        p = float(oof[i])
        level = "High" if p >= 0.45 else ("Medium" if p >= 0.30 else "Low")
        out_rows.append((int(df.referral_id[i]), round(p, 4), level, json.dumps(reasons)))

    db = sqlite3.connect(DB)
    db.execute("DROP TABLE IF EXISTS risk_scores")
    db.execute("""CREATE TABLE risk_scores (referral_id INTEGER PRIMARY KEY,
                  risk_score REAL, risk_level TEXT, top_reasons TEXT)""")
    db.executemany("INSERT INTO risk_scores VALUES (?,?,?,?)", out_rows)
    db.commit()
    lv = pd.Series([r[2] for r in out_rows]).value_counts()
    print("\nRisk levels:", lv.to_dict())
    db.close()

    metrics = {"n_referrals": len(df), "miss_rate": round(float(y.mean()), 3),
               "heldout_benchmark": results, "final_model": final_name,
               "out_of_fold": oof_eval, "calibration": cal, "fairness_top20": fairness,
               "note": "Synthetic data. Numbers show the pipeline works, not real-world accuracy."}
    with open(os.path.join(OUT, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2)
    print("\nSaved model/artifacts/metrics.json, model.joblib and risk_scores table.")


if __name__ == "__main__":
    main()

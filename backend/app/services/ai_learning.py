"""How the AI module learns: a model fitted on an organisation's own history, and corrected by its people.

THE MODEL IS LOGISTIC REGRESSION ON NAMED FACTORS. Each factor is something a
supervisor would recognise ("late 3 of the last 10 shifts", "a night shift",
"9 hours' rest since the last shift"). The fitted weights are kept as plain JSON
in ai_models -- nothing is pickled, so loading a model can never run code -- and
every warning can say which factors raised it and by how much. numpy and scipy
are already in the image; no new library is needed.

TWO LOOPS IMPROVE IT.
  1. Outcomes. Each training run refits on what actually happened (was the
     guard late?), so the model follows the organisation as it changes.
  2. People's decisions. When supervisors dismiss most of a feature's warnings,
     the feature raises its own bar for that organisation; when they accept
     warnings that came true, it lowers it. The bar in force and the reason for
     it are stored with the model, so it is never a mystery.

NOTHING HERE ACTS. This module fits, scores and explains; the feature modules
turn a score into a suggestion, and a person decides.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

#: The chance a warning needs, by the admin's sensitivity setting. HIGH warns
#: more (a lower bar), LOW warns less.
BASE_THRESHOLD = {"HIGH": 0.25, "MEDIUM": 0.40, "LOW": 0.60}
#: How far people's decisions may move the bar from where the admin set it.
MAX_RAISE, MAX_LOWER = 0.20, 0.10
#: How many decided warnings it takes before decisions move the bar at all.
MIN_DECISIONS = 8
#: The most recent decisions considered.
DECISION_WINDOW = 20


@dataclass(frozen=True)
class Fitted:
    factors: list[str]
    weights: list[float]
    intercept: float
    means: list[float]
    scales: list[float]

    def as_json(self) -> dict:
        return {"factors": self.factors, "weights": self.weights, "intercept": self.intercept,
                "means": self.means, "scales": self.scales}

    @classmethod
    def from_json(cls, d: dict) -> "Fitted":
        return cls(factors=list(d["factors"]), weights=[float(w) for w in d["weights"]],
                   intercept=float(d["intercept"]), means=[float(m) for m in d["means"]],
                   scales=[float(s) for s in d["scales"]])


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def fit_logistic(factors: list[str], X: np.ndarray, y: np.ndarray, *, l2: float = 1.0) -> Fitted:
    """Regularised logistic regression on standardised factors.

    Standardising makes the weights comparable (a weight is "per typical
    difference"), and the L2 penalty keeps a small organisation's model from
    believing its first few late shifts too much.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    means = X.mean(axis=0)
    scales = X.std(axis=0)
    scales[scales == 0] = 1.0
    Z = (X - means) / scales
    n, k = Z.shape

    def loss(theta):
        w, b = theta[:k], theta[k]
        p = _sigmoid(Z @ w + b)
        eps = 1e-12
        nll = -np.sum(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps)) / n
        grad_w = Z.T @ (p - y) / n + l2 * w / n
        grad_b = np.sum(p - y) / n
        return nll + 0.5 * l2 * float(w @ w) / n, np.concatenate([grad_w, [grad_b]])

    base = min(max(y.mean(), 1e-3), 1 - 1e-3)
    start = np.concatenate([np.zeros(k), [math.log(base / (1 - base))]])
    res = minimize(loss, start, jac=True, method="L-BFGS-B")
    theta = res.x
    return Fitted(factors=list(factors), weights=[float(v) for v in theta[:k]], intercept=float(theta[k]),
                  means=[float(v) for v in means], scales=[float(v) for v in scales])


def score(model: Fitted, values: dict[str, float]) -> tuple[float, list[dict]]:
    """The chance, and each factor's push on it, largest first.

    A contribution is in log-odds: positive raises the chance, negative lowers it.
    """
    z = model.intercept
    parts = []
    for name, w, m, s in zip(model.factors, model.weights, model.means, model.scales):
        v = float(values.get(name, m))
        c = w * (v - m) / s
        z += c
        parts.append({"factor": name, "value": v, "push": round(c, 4)})
    parts.sort(key=lambda p: p["push"], reverse=True)
    return float(1.0 / (1.0 + math.exp(-z))), parts


def auc(y: np.ndarray, p: np.ndarray) -> float | None:
    """How well the scores rank what happened above what did not (0.5 is guessing).
    None when there is not one of each to compare."""
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    ranks = np.argsort(np.argsort(np.concatenate([pos, neg]))) + 1.0
    # Ties share their average rank.
    allp = np.concatenate([pos, neg])
    for v in np.unique(allp):
        idx = allp == v
        if idx.sum() > 1:
            ranks[idx] = ranks[idx].mean()
    u = ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2
    return float(u / (len(pos) * len(neg)))


def evaluate(factors: list[str], X: np.ndarray, y: np.ndarray, *, l2: float = 1.0) -> dict:
    """Fit on the older four fifths, measure on the newest fifth (rows come oldest
    first), so the figure is how it does on shifts it has not seen."""
    n = len(y)
    cut = int(n * 0.8)
    held = None
    if n - cut >= 10 and 0 < y[:cut].sum() < cut:
        m = fit_logistic(factors, X[:cut], y[:cut], l2=l2)
        probs = np.array([score(m, dict(zip(factors, row)))[0] for row in X[cut:]])
        held = auc(y[cut:], probs)
    return {"rows": int(n), "positives": int(y.sum()), "base_rate": round(float(y.mean()), 4) if n else None,
            "auc_on_newest": round(held, 3) if held is not None else None}


def threshold_for(sensitivity: str, decisions: list[dict],
                  bases: dict[str, float] | None = None, cap: float = 0.9) -> tuple[float, str]:
    """The bar a warning must reach, and why it is there.

    `decisions` are the feature's recent decided warnings, newest first:
    {"status": "ACCEPTED" | "DISMISSED", "outcome": "HAPPENED" | "DID_NOT_HAPPEN" | None}.
    `bases` replaces the chance-based bars for a feature measured on another
    scale (the overtime forecast's share of the monthly limit); `cap` is its top.
    """
    bases = bases or BASE_THRESHOLD
    base = bases.get(sensitivity, bases["MEDIUM"])
    recent = [d for d in decisions if d["status"] in ("ACCEPTED", "DISMISSED")][:DECISION_WINDOW]
    n = len(recent)
    said = f"Set by the sensitivity ({sensitivity.title()})"
    if n < MIN_DECISIONS:
        return base, f"{said}; not enough decisions yet to adjust it ({n} of {MIN_DECISIONS})."
    dismissed = sum(1 for d in recent if d["status"] == "DISMISSED")
    accepted = n - dismissed
    came_true = sum(1 for d in recent if d["status"] == "ACCEPTED" and d.get("outcome") == "HAPPENED")
    if dismissed / n >= 0.6:
        move = MAX_RAISE / 2 if dismissed / n < 0.8 else MAX_RAISE
        why = f"raised because {dismissed} of the last {n} warnings were dismissed"
    elif dismissed / n >= 0.4:
        move, why = 0.05, f"raised a little because {dismissed} of the last {n} warnings were dismissed"
    elif accepted / n >= 0.8 and accepted and came_true / accepted >= 0.5:
        move, why = -0.05, (f"lowered because {accepted} of the last {n} warnings were accepted and "
                            f"{came_true} came true")
    else:
        return base, f"{said}; the last {n} decisions do not call for a change."
    move = max(-MAX_LOWER, min(MAX_RAISE, move))
    value = round(min(cap, max(0.1, base + move)), 2)
    return value, f"{said} at {round(base * 100)}%, {why}."

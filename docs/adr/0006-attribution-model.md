# 6. Attribution model: calibrated multinomial logistic regression

Date: 2026-09-20 · Status: accepted

## Context
Attribution must output a posterior over five classes, the top evidence for/against each, an explicit
"needs human" outcome, and be reproducible from stored evidence. Options: a Bayesian network, calibrated
gradient boosting, or a linear evidence-weighted model.

## Decision
Multinomial logistic regression over ~30 detector-evidence features, temperature-scaled on a validation split.
Chosen because contributions are **exact and additive** (`w_kj * x_j` for the logit of class k), so the
explanation is the model rather than an approximation of it (no SHAP), inference is deterministic, and the
feature vector plus model version fully reproduces a decision. Gradient boosting was rejected: better raw
accuracy on synthetic data is not worth approximate explanations, and it invites overfitting the simulator.

`needs_human` is returned when the top posterior is below `tau` or the top-two margin is below `delta`, both
tuned on validation data and reported as a risk-coverage curve.

## Consequences
- Non-linear interactions must be provided as engineered features (e.g. "sequence regression AND valid tag").
- Trained and tested on simulator scenarios: measures separability inside the simulator, not real-world
  accuracy. Evaluation splits by scenario seed; hard confusable pairs are reported separately.

# Generic controller and estimator decision matrix (FCC Core 1.2)

| Class | Algorithm | Available interface | Appropriate preconditions | What it cannot promise |
|---|---|---|---|---|
| observe | monitor | Engine | Uncertain dynamics or risk | Any change/improvement |
| bounded policy | threshold, rule | Engine | Trusted measurements, bounded known action | Mathematical plant stability |
| human review | human_in_loop / ai_assisted | Engine | Human evidence, external AI advisor as suggestion only | AI guesses as independent measurements |
| classical feedback | pid | Engine SISO, DigitalPID SDK | Dynamic system identified, gain sign/timebase known | General MIMO / stability without tuning |
| discrete constrained optimization | mpc | Engine SISO, Python MIMO | Discrete A/B/C, Q/R, limits, timely x estimate | Guaranteed stability/feasibility |
| linear stochastic estimation | DiscreteKalmanFilter | Python SDK; inside SISO mpc | Linear discrete A/B/C + Q/R/P0, synchronized inputs & sensors | Ground truth, model-correctness proof |
| nonlinear stochastic estimation | EKF / UKF | Python SDK | Registered valid nonlinear f/h model, noise assumptions | Guaranteed nonlinear convergence |
| optimal regulation | discrete_lqr | Python SDK | Stabilizable discrete LTI, meaningful Q/R | Reference tracking or input constraints |
| online identification | RecursiveLeastSquares | Python SDK | Independent y[k], applied u[k], y[k+1] with sufficient excitation | Causal certainty on unexcited data |
| self-tuning control | adaptive_arx / SelfTuningARX | Engine SISO, SDK | Valid ARX; known sign; 12+ verified transitions by default; readback + hard bounds + approval | General adaptive robust stability, MRAC |
| scheduling | fixed/adaptive/cron | Engine.tick | Reliable scheduler clock | Real-time guarantee |
| event trend | TrendTrigger | Python detector | Fresh trusted observations + external event integration | Built-in continuous event polling |

**Selection flow:**

1. If no trusted, timestamped data: monitor and instrument process first.
2. If no defensible process dynamics: use supervised rules/thresholds, never pretend mathematical guarantees.
3. If constrained *linear* dynamics and known model: MPC is viable after feasibility testing. For hidden states, add KF with appropriate noise Q/R.
4. If dynamic behavior is nonlinear: directly integrate registered EKF/UKF as state estimator; do not pass arbitrary user code in a JSON controller config.
5. If observed parameters drift and matched `(y,u,y_next)` samples exist: RLS can estimate; only attempt supervised self-tuning with excitation and hard guards.
6. If high risk or uncertainty remains, use monitor-only or require approval rather than defaulting to the most advanced algorithm.

**Do not confuse:** adaptive *sampling frequency* with adaptive *controller parameters*. Only `adaptive_arx` estimates a response model and proposes revised control values.

"""Domain-neutral numerical foundation for dynamic control systems.

Includes state-space modeling, stability and observability tests, sampled-data
analysis and discrete PID. Conclusions apply to validated mathematical models,
not unknown real-world processes. Software orchestration and AI judgment are
handled separately from this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Literal

import numpy as np


def _matrix(value, label: str) -> np.ndarray:
    matrix = np.asarray(value, dtype=float)
    if matrix.ndim != 2 or not np.all(np.isfinite(matrix)):
        raise ValueError(f"{label} must be a finite 2D matrix")
    return matrix


def _vector(value, n: int, label: str) -> np.ndarray:
    vector = np.asarray(value, dtype=float).reshape(-1)
    if vector.size != n or not np.all(np.isfinite(vector)):
        raise ValueError(f"{label} must contain {n} finite entries")
    return vector


@dataclass(frozen=True)
class StateSpace:
    """x' = Ax + Bu, y = Cx + Du OR x[k+1] = Ax[k] + Bu[k].

    Domain selection is explicit. A continuous model must be discretized before
    using step(); mathematical properties require the model to match the plant.
    """
    A: object
    B: object
    C: object
    D: object | None = None
    domain: Literal["continuous", "discrete"] = "continuous"
    sample_time: float | None = None

    def __post_init__(self):
        a, b, c = (_matrix(getattr(self, key), key) for key in ("A", "B", "C"))
        n = a.shape[0]
        if not n or a.shape != (n, n) or b.shape[0] != n or not b.shape[1] or c.shape[1] != n or not c.shape[0]:
            raise ValueError("Dimension mismatch: A=(n,n), B=(n,m), C=(p,n)")
        d = np.zeros((c.shape[0], b.shape[1])) if self.D is None else _matrix(self.D, "D")
        if d.shape != (c.shape[0], b.shape[1]):
            raise ValueError("D must have shape (number of outputs, number of inputs)")
        if self.domain not in ("continuous", "discrete"):
            raise ValueError("Unknown domain")
        if self.domain == "discrete" and (self.sample_time is None or not math.isfinite(self.sample_time) or self.sample_time <= 0):
            raise ValueError("A discrete model requires a positive finite sample_time")
        for key, val in (("A", a), ("B", b), ("C", c), ("D", d)):
            val.setflags(write=False)
            object.__setattr__(self, key, val)

    @property
    def n(self): return self.A.shape[0]

    @property
    def m(self): return self.B.shape[1]

    @property
    def p(self): return self.C.shape[0]

    def eigenvalues(self) -> np.ndarray:
        return np.linalg.eigvals(self.A)

    def asymptotically_stable(self, *, tol: float = 1e-9) -> bool:
        """Strict eigenvalue criterion only for finite-dimensional LTI model."""
        eig = self.eigenvalues()
        return bool(np.all(eig.real < -tol)) if self.domain == "continuous" else bool(np.all(np.abs(eig) < 1.0 - tol))

    def controllability_matrix(self) -> np.ndarray:
        blocks = [self.B]
        for _ in range(1, self.n):
            blocks.append(self.A @ blocks[-1])
        return np.hstack(blocks)

    def observability_matrix(self) -> np.ndarray:
        blocks = [self.C]
        for _ in range(1, self.n):
            blocks.append(blocks[-1] @ self.A)
        return np.vstack(blocks)

    def is_controllable(self) -> bool:
        return bool(np.linalg.matrix_rank(self.controllability_matrix()) == self.n)

    def is_observable(self) -> bool:
        return bool(np.linalg.matrix_rank(self.observability_matrix()) == self.n)

    def discretize(self, sample_time: float) -> "StateSpace":
        """Exact ZOH conversion using an augmented matrix exponential."""
        if self.domain != "continuous":
            raise ValueError("discretize requires a continuous model")
        if not math.isfinite(sample_time) or sample_time <= 0:
            raise ValueError("sample_time must be positive")
        from scipy.linalg import expm
        aug = np.zeros((self.n + self.m, self.n + self.m))
        aug[:self.n, :self.n] = self.A
        aug[:self.n, self.n:] = self.B
        zoh = expm(aug * sample_time)
        return StateSpace(zoh[:self.n, :self.n], zoh[:self.n, self.n:],
                          self.C.copy(), self.D.copy(), "discrete", sample_time)

    def step(self, x, u) -> tuple[np.ndarray, np.ndarray]:
        """Returns (x[k+1], y[k]) for discrete model and current input u[k]."""
        if self.domain != "discrete":
            raise ValueError("Call discretize() before discrete step")
        xv, uv = _vector(x, self.n, "x"), _vector(u, self.m, "u")
        return self.A @ xv + self.B @ uv, self.C @ xv + self.D @ uv

    def state_feedback(self, gain) -> "StateSpace":
        """Model under u = -Kx + v; v remains an m-dimensional exogenous input."""
        k = _matrix(gain, "K")
        if k.shape != (self.m, self.n):
            raise ValueError("K must be (m,n)")
        return StateSpace(self.A - self.B @ k, self.B, self.C - self.D @ k,
                          self.D, self.domain, self.sample_time)


def place_state_feedback(model: StateSpace, poles) -> np.ndarray:
    """Place eigenvalues of (A-BK); must have controllable model."""
    if not model.is_controllable():
        raise ValueError("System is not controllable; pole assignment unavailable")
    poles = np.asarray(poles, dtype=complex).reshape(-1)
    if len(poles) != model.n or not np.all(np.isfinite(poles)):
        raise ValueError("Need n finite desired poles")
    if model.domain == "continuous" and not np.all(poles.real < 0):
        raise ValueError("Desired poles must be in the open left half plane")
    if model.domain == "discrete" and not np.all(np.abs(poles) < 1):
        raise ValueError("Desired discrete poles must be inside the unit circle")
    from scipy.signal import place_poles
    return np.asarray(np.real_if_close(place_poles(model.A, model.B, poles).gain_matrix))


@dataclass
class LuenbergerObserver:
    """Deterministic full-order observer (no probabilistic Kalman claims).

    Input y[k] measures x[k], and update produces x_hat[k+1]. Observer error
    dynamics are (A-LC), assuming matched model and disturbance-free case.
    """
    model: StateSpace
    gain: object
    x_hat: object

    def __post_init__(self):
        if self.model.domain != "discrete":
            raise ValueError("Use a discrete model with this observer")
        self.gain = _matrix(self.gain, "L")
        if self.gain.shape != (self.model.n, self.model.p):
            raise ValueError("L must have shape (n,p)")
        self.x_hat = _vector(self.x_hat, self.model.n, "x_hat")

    def error_model_stable(self) -> bool:
        return bool(np.all(np.abs(np.linalg.eigvals(self.model.A - self.gain @ self.model.C)) < 1 - 1e-9))

    def update(self, y, u) -> dict:
        yv, uv = _vector(y, self.model.p, "y"), _vector(u, self.model.m, "u")
        predicted_y = self.model.C @ self.x_hat + self.model.D @ uv
        innovation = yv - predicted_y
        self.x_hat = self.model.A @ self.x_hat + self.model.B @ uv + self.gain @ innovation
        return {"state_estimate_next": self.x_hat.copy(), "innovation": innovation.copy(),
                "predicted_measurement": predicted_y.copy()}


def place_observer(model: StateSpace, poles) -> np.ndarray:
    """L for error dynamics A-LC, by dual pole assignment."""
    if not model.is_observable():
        raise ValueError("System is not observable; observer design unavailable")
    poles = np.asarray(poles, dtype=complex).reshape(-1)
    if len(poles) != model.n:
        raise ValueError("Need n observer poles")
    if model.domain == "discrete" and not np.all(np.abs(poles) < 1):
        raise ValueError("Observer poles must be inside the unit circle")
    from scipy.signal import place_poles
    return np.asarray(np.real_if_close(place_poles(model.A.T, model.C.T, poles).gain_matrix.T))


@dataclass
class DigitalPID:
    """SISO discrete PID: derivative on measurement; filtered; clamp & anti-windup.

    u = bias + Kp*(r-y) + Ki*integral(r-y) - Kd*d_filtered(y)/dt.
    Positive-gain plant convention; controller tuning is application-specific.
    This is not a proof of closed-loop stability; tune and validate with a model.
    """
    kp: float
    ki: float = 0.0
    kd: float = 0.0
    derivative_tau: float = 0.0
    integral_min: float = -math.inf
    integral_max: float = math.inf
    output_min: float = -math.inf
    output_max: float = math.inf
    bias: float = 0.0
    integral: float = 0.0
    prev_measurement: float | None = None
    filtered_derivative: float = 0.0

    def __post_init__(self):
        for field_name in ("kp", "ki", "kd", "derivative_tau", "bias", "integral", "filtered_derivative"):
            if not math.isfinite(getattr(self, field_name)):
                raise ValueError(f"{field_name} must be finite")
        if self.derivative_tau < 0 or self.integral_min > self.integral_max or self.output_min > self.output_max:
            raise ValueError("Invalid derivative/filter/integral/output bounds")
        if any(math.isnan(v) for v in (self.integral_min, self.integral_max, self.output_min, self.output_max)):
            raise ValueError("Bounds cannot be NaN")

    def compute(self, setpoint: float, measurement: float, dt: float) -> dict:
        if not all(math.isfinite(x) for x in (setpoint, measurement, dt)) or dt <= 0:
            raise ValueError("setpoint, measurement and dt must be finite; dt > 0")
        error = setpoint - measurement
        derivative = (0.0 if self.prev_measurement is None else
                      (measurement - self.prev_measurement) / dt)
        alpha = 1.0 if self.derivative_tau == 0 else dt / (self.derivative_tau + dt)
        filtered = self.filtered_derivative + alpha * (derivative - self.filtered_derivative)
        next_integral = float(np.clip(self.integral + error * dt, self.integral_min, self.integral_max))
        base_output = self.bias + self.kp * error + self.ki * self.integral - self.kd * filtered
        unsat = self.bias + self.kp * error + self.ki * next_integral - self.kd * filtered
        # Limit *additional* integration at saturation while allowing a first
        # integral step to reach the actuator limit. Naively reverting the whole
        # step here would make a pure-I controller output zero indefinitely.
        if self.ki != 0 and unsat > self.output_max and self.ki * error > 0:
            if base_output >= self.output_max:
                next_integral = self.integral
            else:
                next_integral = self.integral + (self.output_max - base_output) / self.ki
        elif self.ki != 0 and unsat < self.output_min and self.ki * error < 0:
            if base_output <= self.output_min:
                next_integral = self.integral
            else:
                next_integral = self.integral + (self.output_min - base_output) / self.ki
        next_integral = float(np.clip(next_integral, self.integral_min, self.integral_max))
        unsat = self.bias + self.kp * error + self.ki * next_integral - self.kd * filtered
        saturated = float(np.clip(unsat, self.output_min, self.output_max))
        self.integral = next_integral
        self.filtered_derivative = filtered
        self.prev_measurement = measurement
        return {"output": saturated, "unsaturated": unsat, "error": error,
                "integral": self.integral, "derivative": self.filtered_derivative,
                "saturated": not math.isclose(unsat, saturated, abs_tol=1e-12)}

    def snapshot(self) -> dict:
        return {"integral": self.integral, "prev_measurement": self.prev_measurement,
                "filtered_derivative": self.filtered_derivative}


def first_order_arx_identification(y, u, sample_time: float) -> dict:
    """Least-squares identification of y[k+1]=a*y[k]+b*u[k]+c.

    Returns empirical model, NOT certainty about physical causation.
    Requires enough excitation and approximately time-invariant dynamics.
    """
    yy, uu = np.asarray(y, dtype=float).reshape(-1), np.asarray(u, dtype=float).reshape(-1)
    if not math.isfinite(sample_time) or sample_time <= 0:
        raise ValueError("sample_time must be positive")
    if len(yy) != len(uu) + 1 or len(uu) < 5 or not np.all(np.isfinite(yy)) or not np.all(np.isfinite(uu)):
        raise ValueError("Need n+1 finite output samples and n>=5 finite input samples")
    design = np.column_stack([yy[:-1], uu, np.ones(len(uu))])
    if np.linalg.matrix_rank(design) != 3:
        raise ValueError("Insufficiently exciting data; identification not unique")
    coeff, _, _, _ = np.linalg.lstsq(design, yy[1:], rcond=None)
    a, b, c = (float(v) for v in coeff)
    residual = yy[1:] - design @ coeff
    return {"a": a, "b": b, "offset": c, "rmse": float(np.sqrt(np.mean(residual ** 2))),
            "dc_gain": b / (1 - a) if abs(1 - a) > 1e-10 else None,
            "time_constant": -sample_time / math.log(a) if 0 < a < 1 else None,
            "sample_time": sample_time, "model": "first_order_arx_with_offset",
            "note": "Empirical fit; assess validation error on independent data."}

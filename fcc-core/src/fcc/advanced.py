"""Domain-neutral advanced control algorithms (independent of AI and domains).

External extensions to classical control (5e), not chapters from the book:
- discrete linear Kalman filter and nonlinear extended Kalman filter (EKF)
- constrained, receding-horizon linear quadratic MPC
- recursive least squares (RLS) and guarded self-tuning ARX control
- infinite-horizon discrete LQR

All methods require a defensible plant / noise model, synchronized measurements,
actual actuator input and sample time. They DO NOT establish physical safety.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import math

import numpy as np
from scipy.linalg import solve_discrete_are
from scipy.optimize import minimize

from .theory import StateSpace, _matrix, _vector


def _symmetric_psd(value, n: int, label: str, *, strictly_positive: bool = False) -> np.ndarray:
    a = _matrix(value, label)
    if a.shape != (n, n) or not np.allclose(a, a.T, rtol=0, atol=1e-9):
        raise ValueError(f"{label} must be symmetric ({n},{n})")
    smallest = float(np.linalg.eigvalsh(a).min())
    if (smallest <= 1e-12 if strictly_positive else smallest < -1e-9):
        raise ValueError(f"{label} must be {'positive definite' if strictly_positive else 'positive semidefinite'}")
    return a.copy()


def _bounds(value, size: int, name: str):
    if value is None:
        return None
    a = np.asarray(value, dtype=float).reshape(-1)
    if a.size == 1:
        a = np.repeat(a, size)
    if a.size != size or np.any(np.isnan(a)):
        raise ValueError(f"{name} requires a scalar or {size} values, without NaN")
    return a


@dataclass
class DiscreteKalmanFilter:
    """Standard predict/update KF for x[k+1]=Ax[k]+Bu[k]+w, y[k]=Cx[k]+Du[k]+v.

    predict(u[k]) advances prior x[k|k] to x[k+1|k]. update(y[k+1], u[k+1])
    then assimilates observations measured at *that* index. On initialization
    update the first y[k] directly (without predict) if x0 is a prior at k.
    Missing measurement channels are safely skipped; innovation gating rejects
    observations (it is not a proof that rejected measurements were wrong).
    """
    model: StateSpace
    Q: object
    R: object
    x: object
    P: object

    def __post_init__(self):
        if self.model.domain != "discrete":
            raise ValueError("Kalman filter requires a discrete-time StateSpace model")
        self.Q = _symmetric_psd(self.Q, self.model.n, "Q")
        self.R = _symmetric_psd(self.R, self.model.p, "R", strictly_positive=True)
        self.x = _vector(self.x, self.model.n, "x")
        self.P = _symmetric_psd(self.P, self.model.n, "P")

    def predict(self, u):
        uv = _vector(u, self.model.m, "u")
        self.x = self.model.A @ self.x + self.model.B @ uv
        self.P = self.model.A @ self.P @ self.model.A.T + self.Q
        self.P = (self.P + self.P.T) / 2
        return self.snapshot()

    def update(self, y, u=None, *, gate_nis: float | None = None):
        """Assimilate measurement at current x state. y may include None/NaN.

        gate_nis is a user-chosen *absolute* NIS threshold; choose it based on
        the observed channel count, not one unmodified limit for every dimension.
        """
        values = np.asarray(y, dtype=object).reshape(-1)
        if values.size != self.model.p:
            raise ValueError("y dimension mismatch")
        obs = np.array([float('nan') if v is None else float(v) for v in values])
        keep = np.isfinite(obs)
        if not np.all(keep | np.isnan(obs)):
            raise ValueError("Measurement contains infinity")
        if gate_nis is not None and (not np.isfinite(gate_nis) or gate_nis <= 0):
            raise ValueError("gate_nis must be positive")
        if not keep.any():
            return {**self.snapshot(), "used_channels": [], "innovation": [], "nis": None, "accepted": False}
        uv = np.zeros(self.model.m) if u is None else _vector(u, self.model.m, "u")
        C = self.model.C[keep]
        D = self.model.D[keep]
        R = self.R[np.ix_(keep, keep)]
        innovation = obs[keep] - (C @ self.x + D @ uv)
        S = C @ self.P @ C.T + R
        # Two linear solves; no explicit matrix inverse.
        nis = float(innovation @ np.linalg.solve(S, innovation))
        if gate_nis is not None and nis > gate_nis:
            return {**self.snapshot(), "used_channels": np.flatnonzero(keep).tolist(),
                    "innovation": innovation.tolist(), "nis": nis, "accepted": False}
        gain = np.linalg.solve(S, C @ self.P).T
        self.x = self.x + gain @ innovation
        identity = np.eye(self.model.n)
        transition = identity - gain @ C
        # Joseph covariance form: stable under finite precision.
        self.P = transition @ self.P @ transition.T + gain @ R @ gain.T
        self.P = (self.P + self.P.T) / 2
        return {**self.snapshot(), "used_channels": np.flatnonzero(keep).tolist(),
                "innovation": innovation.tolist(), "nis": nis, "accepted": True}

    def snapshot(self):
        return {"x": self.x.tolist(), "P": self.P.tolist()}


@dataclass
class ExtendedKalmanFilter:
    """EKF for nonlinear x_next=f(x,u), y=h(x,u). Analytic Jacobians are required.

    EKF is a local approximation; divergence and inconsistent models are possible.
    Only first-order linearization is implemented; this is NOT a UKF.
    """
    f: Callable
    h: Callable
    jac_f: Callable
    jac_h: Callable
    Q: object
    R: object
    x: object
    P: object

    def __post_init__(self):
        self.x = np.asarray(self.x, dtype=float).reshape(-1)
        if not self.x.size or not np.all(np.isfinite(self.x)):
            raise ValueError("EKF x must be a nonempty finite vector")
        n = len(self.x)
        self.P = _symmetric_psd(self.P, n, "P")
        self.Q = _symmetric_psd(self.Q, n, "Q")
        self.R = _matrix(self.R, "R")
        if self.R.shape[0] != self.R.shape[1]:
            raise ValueError("EKF R must be square")
        self.R = _symmetric_psd(self.R, self.R.shape[0], "R", strictly_positive=True)

    def predict(self, u):
        old = self.x.copy()
        F = _matrix(self.jac_f(old, u), "jac_f")
        if F.shape != (len(old), len(old)):
            raise ValueError("jac_f dimension mismatch")
        next_x = _vector(self.f(old, u), len(old), "f(x,u)")
        self.x = next_x
        self.P = F @ self.P @ F.T + self.Q
        self.P = (self.P + self.P.T) / 2
        return self.snapshot()

    def update(self, y, u, *, gate_nis: float | None = None):
        p = self.R.shape[0]
        measurement = _vector(y, p, "y")
        H = _matrix(self.jac_h(self.x, u), "jac_h")
        if H.shape != (p, len(self.x)):
            raise ValueError("jac_h dimension mismatch")
        innovation = measurement - _vector(self.h(self.x, u), p, "h(x,u)")
        S = H @ self.P @ H.T + self.R
        nis = float(innovation @ np.linalg.solve(S, innovation))
        if gate_nis is not None and (not math.isfinite(gate_nis) or gate_nis <= 0):
            raise ValueError("gate_nis must be positive")
        if gate_nis is not None and nis > gate_nis:
            return {**self.snapshot(), "innovation": innovation.tolist(), "nis": nis, "accepted": False}
        K = np.linalg.solve(S, H @ self.P).T
        self.x = self.x + K @ innovation
        I_KH = np.eye(len(self.x)) - K @ H
        self.P = I_KH @ self.P @ I_KH.T + K @ self.R @ K.T
        self.P = (self.P + self.P.T) / 2
        return {**self.snapshot(), "innovation": innovation.tolist(), "nis": nis, "accepted": True}

    def snapshot(self):
        return {"x": self.x.tolist(), "P": self.P.tolist()}


def discrete_lqr(model: StateSpace, Q, R) -> dict:
    """Infinite-horizon state-regulating discrete LQR, u=-Kx.

    Q>=0, R>0; Riccati equation must have a stabilizing solution. Verify the
    returned closed-loop eigenvalues; no reference feedforward is provided.
    """
    if model.domain != "discrete":
        raise ValueError("LQR requires a discrete model")
    q = _symmetric_psd(Q, model.n, "Q")
    r = _symmetric_psd(R, model.m, "R", strictly_positive=True)
    P = solve_discrete_are(model.A, model.B, q, r)
    K = np.linalg.solve(r + model.B.T @ P @ model.B, model.B.T @ P @ model.A)
    eig = np.linalg.eigvals(model.A - model.B @ K)
    if np.max(np.abs(eig)) >= 1-1e-9:
        raise ValueError("Computed LQR closed-loop is not strictly stable")
    return {"K": K, "P": P, "closed_loop_poles": eig}


@dataclass
class ConstrainedMPC:
    """Finite horizon linear output-tracking MPC with input, delta-u, output bounds.

    min Σ(y[k+1]-r[k+1])'Q(y-r) + (u[k]-u_ref)'R(u-u_ref)
        + (u[k]-u[k-1])'S(u-u_prev) and terminal output cost Qf.
    Solves at each step with SLSQP; only FIRST action is eligible for execution.
    This does not guarantee recursive feasibility or closed-loop stability.
    """
    model: StateSpace
    horizon: int
    Q: object
    R: object
    S: object | None = None
    Qf: object | None = None
    u_min: object | None = None
    u_max: object | None = None
    du_min: object | None = None
    du_max: object | None = None
    y_min: object | None = None
    y_max: object | None = None
    maxiter: int = 200

    def __post_init__(self):
        if self.model.domain != "discrete":
            raise ValueError("MPC requires a discrete model and known sample_time")
        if not isinstance(self.horizon, int) or not 1 <= self.horizon <= 200:
            raise ValueError("horizon must be an integer in [1,200]")
        if not isinstance(self.maxiter, int) or self.maxiter < 1:
            raise ValueError("maxiter must be positive")
        self.Q = _symmetric_psd(self.Q, self.model.p, "Q")
        self.R = _symmetric_psd(self.R, self.model.m, "R", strictly_positive=True)
        self.S = (np.zeros_like(self.R) if self.S is None else _symmetric_psd(self.S, self.model.m, "S"))
        self.Qf = self.Q if self.Qf is None else _symmetric_psd(self.Qf, self.model.p, "Qf")
        for name, size in (("u_min", self.model.m), ("u_max", self.model.m),
                           ("du_min", self.model.m), ("du_max", self.model.m),
                           ("y_min", self.model.p), ("y_max", self.model.p)):
            setattr(self, name, _bounds(getattr(self, name), size, name))
        for lo, hi in ((self.u_min, self.u_max), (self.du_min, self.du_max), (self.y_min, self.y_max)):
            if lo is not None and hi is not None and np.any(lo > hi):
                raise ValueError("lower bound exceeds upper bound")

    def _prediction(self, x0):
        a, b, c, d = self.model.A, self.model.B, self.model.C, self.model.D
        N, m, p = self.horizon, self.model.m, self.model.p
        base = np.zeros(N * p)
        G = np.zeros((N * p, N * m))
        powers = [np.eye(self.model.n)]
        for _ in range(N):
            powers.append(powers[-1] @ a)
        for i in range(N):
            base[i*p:(i+1)*p] = c @ powers[i+1] @ x0
            for j in range(i+1):
                G[i*p:(i+1)*p, j*m:(j+1)*m] = c @ powers[i-j] @ b
                if i == j:
                    G[i*p:(i+1)*p, j*m:(j+1)*m] += d
        return base, G

    def solve(self, x0, reference, u_prev, *, u_reference=None, warm_start=None):
        x = _vector(x0, self.model.n, "x0")
        prev = _vector(u_prev, self.model.m, "u_prev")
        N, m, p = self.horizon, self.model.m, self.model.p
        ref = np.asarray(reference, dtype=float)
        if ref.shape == (p,):
            ref = np.tile(ref, (N, 1))
        if ref.shape != (N, p) or not np.all(np.isfinite(ref)):
            raise ValueError(f"reference must be ({p},) or ({N},{p}) and finite")
        uref = np.zeros(m) if u_reference is None else _vector(u_reference, m, "u_reference")
        base, G = self._prediction(x)
        # Block diagonal weighting, plus first-difference cost.
        qweights = [self.Q for _ in range(N-1)] + [self.Q + self.Qf]
        Qblk = np.zeros((N*p, N*p))
        Rblk = np.kron(np.eye(N), self.R)
        Sblk = np.kron(np.eye(N), self.S)
        for k, w in enumerate(qweights):
            Qblk[k*p:(k+1)*p, k*p:(k+1)*p] = w
        T = np.eye(N*m)
        for k in range(1, N):
            T[k*m:(k+1)*m, (k-1)*m:k*m] = -np.eye(m)
        past = np.zeros(N*m)
        past[:m] = prev
        target = ref.reshape(-1)
        target_u = np.tile(uref, N)
        H = G.T @ Qblk @ G + Rblk + T.T @ Sblk @ T
        linear = G.T @ Qblk @ (base-target) - Rblk @ target_u - T.T @ Sblk @ past
        def cost(z):
            return float(z @ H @ z + 2 * linear @ z)
        def grad(z):
            return 2*(H @ z + linear)
        constraints = []
        if self.du_min is not None or self.du_max is not None:
            lo = np.tile(self.du_min if self.du_min is not None else np.repeat(-np.inf, m), N) + past
            hi = np.tile(self.du_max if self.du_max is not None else np.repeat(np.inf, m), N) + past
            constraints.append({'type':'ineq', 'fun': lambda z, lo=lo: T @ z-lo,
                                'jac': lambda z: T})
            constraints.append({'type':'ineq', 'fun': lambda z, hi=hi: hi-T @ z,
                                'jac': lambda z: -T})
        if self.y_min is not None:
            ylo = np.tile(self.y_min, N)
            constraints.append({'type':'ineq','fun': lambda z, ylo=ylo: base+G@z-ylo,
                                'jac': lambda z: G})
        if self.y_max is not None:
            yhi = np.tile(self.y_max, N)
            constraints.append({'type':'ineq','fun': lambda z, yhi=yhi: yhi-base-G@z,
                                'jac': lambda z: -G})
        lb = self.u_min if self.u_min is not None else np.full(m, -np.inf)
        ub = self.u_max if self.u_max is not None else np.full(m, np.inf)
        bounds = list(zip(np.tile(lb, N), np.tile(ub, N)))
        start = np.tile(prev, N) if warm_start is None else _vector(warm_start, N*m, 'warm_start')
        start = np.clip(start, np.tile(lb, N), np.tile(ub, N))
        sol = minimize(cost, start, jac=grad, method="SLSQP", bounds=bounds,
                       constraints=constraints, options={'maxiter':self.maxiter,'ftol':1e-9,'disp':False})
        if not sol.success or not np.all(np.isfinite(sol.x)):
            raise RuntimeError(f"MPC infeasible or optimization failed: {sol.message}")
        z = np.asarray(sol.x)
        if (np.any(z < np.tile(lb, N)-1e-5) or np.any(z > np.tile(ub, N)+1e-5)
                or any(np.any(c['fun'](z) < -1e-5) for c in constraints)):
            raise RuntimeError("MPC optimizer returned a constraint violation")
        plan = z.reshape(N, m)
        predicted_y=(base+G@z).reshape(N,p)
        residual=predicted_y.reshape(-1)-target
        differences=T@z-past
        absolute_cost=float(residual@Qblk@residual +
                            (z-target_u)@Rblk@(z-target_u) +
                            differences@Sblk@differences)
        return {'first_control': plan[0].copy(), 'plan': plan.copy(),
                'predicted_outputs': predicted_y,
                'objective_value': absolute_cost,
                'success': True, 'iterations':int(sol.nit),
                'note': 'Model prediction, not verified plant outcome'}


@dataclass
class RecursiveLeastSquares:
    """RLS parameter estimator θ for y_next = φ'θ, forgetting_factor λ∈(0,1].

    Online least-squares estimation; it is NOT automatically a stable controller.
    Call only on independent, time-aligned measured transitions.
    """
    theta: object
    P: object
    forgetting_factor: float = 0.99

    def __post_init__(self):
        self.theta = np.asarray(self.theta, dtype=float).reshape(-1)
        if not self.theta.size or not np.all(np.isfinite(self.theta)):
            raise ValueError("RLS theta must be a nonempty finite vector")
        self.P = _symmetric_psd(self.P, len(self.theta), 'P', strictly_positive=True)
        if not 0 < self.forgetting_factor <= 1:
            raise ValueError('forgetting_factor must be in (0,1]')

    def update(self, phi, y_next):
        reg = _vector(phi, len(self.theta), 'phi')
        yv = float(y_next)
        if not math.isfinite(yv):
            raise ValueError('y_next must be finite')
        denom = self.forgetting_factor + reg @ self.P @ reg
        gain = self.P @ reg / denom
        residual = yv - reg @ self.theta
        self.theta = self.theta + gain*residual
        self.P = (self.P - np.outer(gain, reg) @ self.P) / self.forgetting_factor
        self.P = (self.P+self.P.T)/2
        if not np.all(np.isfinite(self.P)) or not np.all(np.isfinite(self.theta)):
            raise RuntimeError('RLS numerical instability')
        return {'theta':self.theta.copy(), 'residual':float(residual),
                'variance_proxy': float(reg @ self.P @ reg), 'gain':gain.copy()}

    def snapshot(self):
        return {'theta':self.theta.tolist(), 'P':self.P.tolist(),
                'forgetting_factor':self.forgetting_factor}


@dataclass
class SelfTuningARX:
    """Guarded SISO certainty-equivalence controller for y_next=a*y+b*u+c.

    Identifies a,b,c using RLS. Suggests an action only if model confidence,
    sign, parameter and actuator bounds pass; no stability guarantee.
    Use in simulation + supervised trial before enabling actuation.
    """
    rls: RecursiveLeastSquares
    expected_gain_sign: int
    min_abs_gain: float = 0.03
    max_abs_a: float = 2.0
    max_abs_gain: float = 100.0
    min_samples: int = 12
    samples: int = 0

    def __post_init__(self):
        if len(self.rls.theta) != 3:
            raise ValueError('ARX theta must be [a,b,offset]')
        if self.expected_gain_sign not in (-1,1):
            raise ValueError('expected_gain_sign must be -1 or 1')
        if self.min_abs_gain <= 0 or self.max_abs_gain <= self.min_abs_gain:
            raise ValueError('Invalid gain boundaries')
        if self.min_samples < 3:
            raise ValueError('min_samples must be >= 3')

    def observe_transition(self, y_prev: float, u_applied: float, y_next: float):
        updated = self.rls.update([y_prev,u_applied,1.0], y_next)
        self.samples += 1
        return updated

    def propose(self, y_now: float, target_next: float, u_current: float,
                *, u_min:float, u_max:float, max_step:float):
        if self.samples < self.min_samples:
            raise RuntimeError(f'Insufficient verified transitions: {self.samples}/{self.min_samples}')
        a, b, c = (float(v) for v in self.rls.theta)
        if (not all(map(math.isfinite, (y_now,target_next,u_current,u_min,u_max,max_step)))
            or not 0 < max_step or u_min > u_max):
            raise ValueError('Invalid control input/bounds')
        if abs(a) > self.max_abs_a or not self.min_abs_gain <= abs(b) <= self.max_abs_gain or b*self.expected_gain_sign <= 0:
            raise RuntimeError('Identified response is unsafe/poorly conditioned or changed sign')
        raw = (target_next - a*y_now - c) / b
        lo=max(u_min,u_current-max_step)
        hi=min(u_max,u_current+max_step)
        if lo>hi:
            raise RuntimeError('No feasible actuator input within current step and global bounds')
        proposal = float(np.clip(raw, lo, hi))
        return {'control':proposal, 'unbounded':float(raw),
                'predicted_output':a*y_now+b*proposal+c,
                'model':[a,b,c], 'limited':not np.isclose(proposal,raw),
                'note':'Adaptive estimate; requires independent output verification'}

    def snapshot(self):
        return {'rls':self.rls.snapshot(), 'samples':self.samples}

@dataclass
class UnscentedKalmanFilter:
    """Scaled unscented nonlinear filter with additive process/measurement noise.

    f(x,u), h(x,u), explicit measurement dimension from R. Uses sigma points
    and noise covariances Q/R, with local PSD enforcement to combat roundoff.
    A valid noise model and actual actuator timing are still prerequisites.
    """
    f: Callable
    h: Callable
    Q: object
    R: object
    x: object
    P: object
    alpha: float = 0.3
    beta: float = 2.0
    kappa: float = 0.0

    def __post_init__(self):
        self.x = np.asarray(self.x,dtype=float).reshape(-1)
        if not self.x.size or not np.all(np.isfinite(self.x)):
            raise ValueError("UKF requires finite nonempty initial state")
        n=len(self.x)
        self.Q=_symmetric_psd(self.Q,n,'Q')
        self.P=_symmetric_psd(self.P,n,'P')
        self.R=_matrix(self.R,'R')
        self.R=_symmetric_psd(self.R,self.R.shape[0],'R',strictly_positive=True)
        if not (0 < self.alpha <= 1) or not self.beta >= 0 or n+self.kappa <= 0:
            raise ValueError('Invalid UKF alpha/beta/kappa')
        lam=self.alpha**2*(n+self.kappa)-n
        self.scale=n+lam
        self.Wm=np.full(2*n+1,1/(2*self.scale))
        self.Wc=self.Wm.copy()
        self.Wm[0]=lam/self.scale
        self.Wc[0]=self.Wm[0]+(1-self.alpha**2+self.beta)
        self._sigma_pred=None

    def _sigma(self,x,P):
        n=len(x)
        jitter=0.
        for _ in range(9):
            try:
                L=np.linalg.cholesky(self.scale*(P+np.eye(n)*jitter))
                return np.vstack([x, np.array([x+L[:,i] for i in range(n)]),
                                  np.array([x-L[:,i] for i in range(n)])])
            except np.linalg.LinAlgError:
                jitter=1e-12 if not jitter else jitter*10
        raise ValueError('UKF covariance is not numerically positive semidefinite')

    def _weighted_cov(self, points, mean):
        centered=points-mean
        return (centered*self.Wc[:,None]).T @ centered

    @staticmethod
    def _psd_project(mat):
        mat=(mat+mat.T)/2
        if not np.all(np.isfinite(mat)):
            raise ValueError('UKF covariance became nonfinite')
        eig,V=np.linalg.eigh(mat)
        if np.min(eig)<-1e-5:
            raise ValueError('UKF covariance diverged (significant negative eigenvalue)')
        return (V*np.clip(eig,0,None))@V.T

    def predict(self,u):
        sigma=self._sigma(self.x,self.P)
        predicted=np.array([_vector(self.f(point,u),len(self.x),'f') for point in sigma])
        self.x=self.Wm@predicted
        self.P=self._psd_project(self._weighted_cov(predicted,self.x)+self.Q)
        # sigma should reflect predicted prior with process noise for h(x,u).
        self._sigma_pred=self._sigma(self.x,self.P)
        return self.snapshot()

    def update(self,y,u, *, gate_nis:float|None=None):
        p=self.R.shape[0]
        obs=_vector(y,p,'y')
        if gate_nis is not None and (not np.isfinite(gate_nis) or gate_nis<=0):
            raise ValueError('gate_nis must be positive')
        sigma=self._sigma(self.x,self.P) if self._sigma_pred is None else self._sigma_pred
        ypts=np.array([_vector(self.h(point,u),p,'h') for point in sigma])
        predicted=self.Wm@ypts
        innovation=obs-predicted
        S=self._psd_project(self._weighted_cov(ypts,predicted)+self.R)
        xdiff=sigma-self.x
        ydiff=ypts-predicted
        cross=(xdiff*self.Wc[:,None]).T @ ydiff
        nis=float(innovation@np.linalg.solve(S,innovation))
        if gate_nis is not None and nis>gate_nis:
            return {**self.snapshot(),'innovation':innovation.tolist(), 'nis':nis,'accepted':False}
        K=np.linalg.solve(S,cross.T).T
        self.x=self.x+K@innovation
        self.P=self._psd_project(self.P-K@S@K.T)
        self._sigma_pred=None
        return {**self.snapshot(),'innovation':innovation.tolist(),'nis':nis,'accepted':True}

    def snapshot(self):
        return {'x':self.x.tolist(),'P':self.P.tolist()}

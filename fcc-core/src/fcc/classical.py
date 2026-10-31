"""General LTI analysis routines aligned with textbook control topics.

These are analytical / simulation instruments; they are NOT plant certification.
Models must be identified and their bandwidth/timebase independently verified.
"""
from __future__ import annotations

import numpy as np
from scipy import signal
from scipy.linalg import solve_continuous_lyapunov, solve_discrete_lyapunov

from .theory import StateSpace, _matrix, _vector
from .advanced import _symmetric_psd


def linearize(f, x0, u0, *, epsilon:float=1e-5, sample_time:float|None=None) -> StateSpace:
    """Central finite-difference Jacobians for f(x,u) -> xdot OR x_next.

    Finite differences are local approximations, especially for discontinuous
    systems. Output is x itself (C=I, D=0). Use StateSpace directly if h(x,u).
    """
    x=np.asarray(x0,dtype=float).reshape(-1)
    u=np.asarray(u0,dtype=float).reshape(-1)
    if not x.size or not u.size or not np.all(np.isfinite(x)) or not np.all(np.isfinite(u)) or not np.isfinite(epsilon) or epsilon<=0:
        raise ValueError('linearize requires finite vectors and positive epsilon')
    n,m=len(x),len(u)
    A=np.zeros((n,n));B=np.zeros((n,m))
    for i in range(n):
        delta=np.zeros(n);delta[i]=epsilon*max(1,abs(x[i]))
        A[:,i]=(_vector(f(x+delta,u),n,'f+')-_vector(f(x-delta,u),n,'f-'))/(2*delta[i])
    for j in range(m):
        delta=np.zeros(m);delta[j]=epsilon*max(1,abs(u[j]))
        B[:,j]=(_vector(f(x,u+delta),n,'f+')-_vector(f(x,u-delta),n,'f-'))/(2*delta[j])
    return StateSpace(A,B,np.eye(n),domain='discrete' if sample_time is not None else 'continuous',sample_time=sample_time)


def state_space_to_transfer_function(model:StateSpace):
    """SISO transfer function (numerator,denominator); includes direct feedthrough."""
    if model.m!=1 or model.p!=1:
        raise ValueError('Transfer function helper is for SISO only')
    num,den=signal.ss2tf(model.A,model.B,model.C,model.D,input=0)
    return {'numerator':np.trim_zeros(num[0], 'f') if np.any(num[0]) else np.array([0.]),
            'denominator':den.copy(),'domain':model.domain,'sample_time':model.sample_time}


def frequency_response(model:StateSpace, angular_frequencies):
    """Complex response H(jω) or H(exp(jωT)); angular_frequencies rad/s.

    Discrete evaluation is valid only at frequencies below Nyquist π/T.
    Returns array (n_freq,p,m) with magnitudes/phase. No causal assumption
    about measurements nor sensor dynamics is inferred.
    """
    f=np.asarray(angular_frequencies,dtype=float).reshape(-1)
    if not f.size or np.any(~np.isfinite(f)) or np.any(f<0):
        raise ValueError('Frequencies must be nonnegative finite values')
    if model.domain=='discrete' and np.any(f>np.pi/model.sample_time+1e-12):
        raise ValueError('Discrete frequencies must be <= Nyquist')
    n=model.n
    results=[]
    for omega in f:
        pole = 1j*omega if model.domain=='continuous' else np.exp(1j*omega*model.sample_time)
        H=model.C@np.linalg.solve(pole*np.eye(n)-model.A,model.B)+model.D
        results.append(H)
    z=np.stack(results)
    return {'frequency_rad_per_s':f, 'complex_response':z,
            'magnitude':np.abs(z),'magnitude_db':20*np.log10(np.maximum(np.abs(z),1e-300)),
            'phase_deg':np.angle(z,deg=True)}


def lyapunov_certificate(model:StateSpace, Q):
    """Positive definite P satisfying continuous A'P+PA=-Q or discrete A'PA-P=-Q.

    Returns certificate for strict LTI asymptotic stability when Q>0 and P>0.
    May be ill-conditioned for nearly marginal poles; never apply to model-free AI.
    """
    q=_symmetric_psd(Q,model.n,'Q',strictly_positive=True)
    if model.domain=='continuous':
        P=solve_continuous_lyapunov(model.A.T,-q)
        residual=model.A.T@P+P@model.A+q
    else:
        P=solve_discrete_lyapunov(model.A.T,q)
        residual=model.A.T@P@model.A-P+q
    P=(P+P.T)/2
    eigen=np.linalg.eigvalsh(P)
    max_err=float(np.linalg.norm(residual,ord='fro'))
    return {'P':P,'positive_definite':bool(eigen.min()>1e-9),
            'residual_norm':max_err,'certificate_valid':bool(eigen.min()>1e-9 and max_err<1e-6*max(1,float(np.linalg.norm(q))))}


def step_response(model:StateSpace, *, steps:int=60, amplitude:float=1.0, x0=None):
    """Zero-order-held SISO step output and state response, for model validation."""
    if model.m!=1 or model.p!=1:
        raise ValueError('step_response currently SISO only')
    if steps<1 or not np.isfinite(amplitude):
        raise ValueError('Invalid steps/amplitude')
    if model.domain=='continuous':
        model=model.discretize(0.01 if model.sample_time is None else model.sample_time)
    x=np.zeros(model.n) if x0 is None else _vector(x0,model.n,'x0')
    xs=[x.copy()]; ys=[]
    for _ in range(steps):
        x,y=model.step(x,[amplitude]);ys.append(float(y[0]));xs.append(x.copy())
    return {'time':np.arange(steps)*model.sample_time,
            'output':np.asarray(ys),'state':np.asarray(xs),
            'note':'ZOH model simulation; NOT an observed real-plant response'}

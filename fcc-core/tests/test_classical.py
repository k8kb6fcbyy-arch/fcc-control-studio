import numpy as np
import pytest
from numpy.testing import assert_allclose
from fcc.classical import linearize, state_space_to_transfer_function, frequency_response, lyapunov_certificate,step_response
from fcc.theory import StateSpace


def test_linearize_continuous_jacobian():
    m=linearize(lambda x,u:np.array([-2*x[0]+3*u[0]+x[0]**2]),[0.],[0.])
    assert_allclose(m.A,[[-2]],atol=1e-8)
    assert_allclose(m.B,[[3]],atol=1e-8)


def test_transfer_and_frequency_response_known_first_order():
    m=StateSpace([[-2]],[[4]],[[1.]])
    tf=state_space_to_transfer_function(m)
    assert_allclose(tf['numerator'],[4])
    assert_allclose(tf['denominator'],[1,2])
    resp=frequency_response(m,[0,2])
    assert resp['magnitude'][0,0,0]==pytest.approx(2.)
    assert resp['magnitude'][1,0,0]==pytest.approx(4/np.sqrt(8))


def test_discrete_nyquist_guard():
    m=StateSpace([[.8]],[[.2]],[[1.]],domain='discrete',sample_time=.1)
    with pytest.raises(ValueError,match='Nyquist'):
        frequency_response(m,[100])


def test_lyapunov_certificate_only_for_stable_LTI():
    stable=StateSpace([[-1,0],[0,-2]],[[1],[1]],[[1,0]])
    cert=lyapunov_certificate(stable,np.eye(2))
    assert cert['certificate_valid']
    bad=StateSpace([[1.]],[[1.]],[[1.]])
    assert lyapunov_certificate(bad,[[1.]])['certificate_valid'] is False
    discrete=StateSpace([[.8]],[[1.]],[[1.]],domain='discrete',sample_time=1.)
    assert lyapunov_certificate(discrete,[[1.]])['certificate_valid']


def test_step_response_is_deterministic():
    m=StateSpace([[.5]],[[.5]],[[1.]],domain='discrete',sample_time=1.)
    res=step_response(m,steps=4)
    assert_allclose(res['output'],[0,.5,.75,.875])

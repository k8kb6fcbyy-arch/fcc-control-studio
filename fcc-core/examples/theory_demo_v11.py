"""Neutral one-state closed-loop numerical demo; NO real equipment/industry adapter.

Illustrative plant x' = -x + u; numerical simulation is not a safety proof.
"""
from __future__ import annotations
from fcc.theory import StateSpace, DigitalPID


def run():
    dt=0.05
    plant=StateSpace([[-1.0]],[[1.0]],[[1.0]]).discretize(dt)
    controller=DigitalPID(kp=2.0,ki=0.6,kd=0.0,output_min=0,output_max=3)
    state=[0.0]
    history=[]
    for k in range(400):
        y=float(plant.C[0,0]*state[0])
        u=controller.compute(setpoint=1.0,measurement=y,dt=dt)['output']
        next_state,_=plant.step(state,[u])
        state=next_state.tolist()
        history.append((k*dt,y,u))
    print('sample_count:',len(history))
    print('final_measurement:',round(history[-1][1],6))
    print('setpoint:',1.0,'| error:',round(1.0-history[-1][1],6))
    assert abs(1.0-history[-1][1]) < 0.01

if __name__=='__main__':run()

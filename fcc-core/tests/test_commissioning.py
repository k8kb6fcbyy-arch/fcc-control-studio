"""1.4 通用需求建模: 不依赖特定业务，验证所有门槛和持久报告。"""
import datetime as dt
import pytest
from fastapi.testclient import TestClient
from fcc.server import create_app
from fcc.commissioning import Intake, compile_plan, next_questions, CommissioningService
from fcc import util


def payload(**updates):
    p=dict(title="Example",desired_outcome="Maintain a measurable outcome",
           metric_name="M1",target_value=100.0,target_mode="at_least",tolerance=0,
           data_source="human_input",sampling_seconds=60,evaluation_seconds=60,
           report_seconds=300,action_authority="suggest")
    p.update(updates)
    return p


def test_progressive_questions_skip_optional():
    with TestClient(create_app(':memory:')) as api:
        h=api.get('/api/health').json()
        assert h['scheduler_running'] is False
        r=api.post('/api/onboarding/plans',json={}).json()
        assert r['required_missing'][0]=='title'
        assert {q['step'] for q in r['next_questions']}=={1}
        lid=r['plan_id']
        r=api.patch(f'/api/onboarding/plans/{lid}',json={'title':'G','desired_outcome':'O'}).json()
        assert {q['step'] for q in r['next_questions']}=={2}
        assert api.get(f'/api/onboarding/plans/{lid}/compile').status_code==422


def test_compilation_never_promotes_text_to_actuator():
    result=compile_plan(Intake.model_validate(payload(controllable_levers=['some process'],action_authority='approval_required')))
    c=result['loop_config']
    assert c['controller']['kind']=='monitor'
    assert c['actuator']['permission_mode']=='suggest_only'
    assert c['goals'][0]['direction']=='increase'
    assert c['scheduler']['mode']=='fixed'
    assert result['requires_first_verified_sample']
    with pytest.raises(ValueError):
        compile_plan(Intake.model_validate(payload(controllable_levers=[],action_authority='approval_required')))


def test_input_frequency_mismatch():
    with pytest.raises(ValueError):
        Intake.model_validate(payload(sampling_seconds=3600,evaluation_seconds=60))


def test_lifecycle_and_monitoring(tmp_path):
    db=str(tmp_path/'case.db')
    with TestClient(create_app(db)) as api:
        doc=api.post('/api/onboarding/plans',json=payload()).json();pid=doc['plan_id']
        result=api.post(f'/api/onboarding/plans/{pid}/activate').json()
        assert result['status']=='running'
        assert api.post(f'/api/onboarding/plans/{pid}/activate').json()['already_activated']
        lid=result['loop_id']
        assert api.get(f'/api/loops/{lid}').json()['enabled']
        assert api.post(f'/api/onboarding/plans/{pid}/observations',json={'source_type':'measured','value':110}).status_code==422
        for x in [95,105]:
            assert api.post(f'/api/onboarding/plans/{pid}/observations',json={'source_type':'human_input','value':x}).status_code==200
        s=api.get(f'/api/onboarding/plans/{pid}/status').json()
        assert s['value']==105 and s['out_of_tolerance'] is False
        assert s['sustained_success'] is False  # too many rapid repeats cannot prove stability
        api.post(f'/api/onboarding/plans/{pid}/pause')
        assert api.get(f'/api/onboarding/plans/{pid}').json()['status']=='paused'
        api.post(f'/api/onboarding/plans/{pid}/resume')
        assert api.get(f'/api/onboarding/plans/{pid}').json()['status']=='running'
        assert api.patch(f'/api/onboarding/plans/{pid}',json={'target_value':120}).status_code==422
    # 数据落盘，服务重启后仍可读取项目
    with TestClient(create_app(db)) as api:
        assert api.get(f'/api/onboarding/plans/{pid}').json()['loop_id']==lid
        assert api.get(f'/api/onboarding/plans/{pid}/status').json()['value']==105


def test_ai_only_never_claim_success():
    with TestClient(create_app(':memory:')) as api:
        doc=api.post('/api/onboarding/plans',json=payload(data_source='ai_inference',sampling_seconds=.001,evaluation_seconds=.001)).json()
        pid=doc['plan_id'];api.post(f'/api/onboarding/plans/{pid}/activate')
        for i in range(3):
            r=api.post(f'/api/onboarding/plans/{pid}/observations',json={'source_type':'ai_inference','value':150,'confidence':.99,'evidence':['model supplied estimate']})
            assert r.status_code==200
        s=api.get(f'/api/onboarding/plans/{pid}/status').json()
        assert s['sustained_success'] is False and s['current_sample_trusted'] is False


def test_periodic_checkpoints_are_durable_and_pause_stops_them(tmp_path):
    with TestClient(create_app(str(tmp_path/'db.sqlite'))) as api:
        doc=api.post('/api/onboarding/plans',json=payload(report_seconds=3)).json();pid=doc['plan_id']
        api.post(f'/api/onboarding/plans/{pid}/activate')
        service=api.app.state.commissioning
        row=service._row(pid)
        past=util.to_iso(util.parse_iso(row['last_report_at'])+dt.timedelta(seconds=4))
        made=service.tick(now=past)
        assert made==[pid]
        reports=api.get(f'/api/onboarding/plans/{pid}/outbox').json()['reports']
        assert len(reports)==1 and reports[0]['payload']['checkpoint']['current_sample_trusted'] is False
        assert service.tick(now=past)==[]
        rid=reports[0]['id']; assert api.post(f'/api/onboarding/plans/{pid}/outbox/{rid}/ack').json()['ok']
        assert api.get(f'/api/onboarding/plans/{pid}/outbox?unread_only=true').json()['reports']==[]
        api.post(f'/api/onboarding/plans/{pid}/pause')
        assert service.tick(now=util.to_iso(util.parse_iso(past)+dt.timedelta(hours=1)))==[]


def test_intervention_requires_separate_confirmation_and_after_readback():
    with TestClient(create_app(':memory:')) as api:
        pid=api.post('/api/onboarding/plans',json=payload()).json()['plan_id']
        api.post(f'/api/onboarding/plans/{pid}/activate')
        obs=f'/api/onboarding/plans/{pid}/observations'
        assert api.post(obs,json={'source_type':'human_input','value':80}).status_code==200
        proposed=api.post(f'/api/onboarding/plans/{pid}/interventions',json={'description':'Change a permitted setting'}).json()
        assert proposed['status']=='proposed'
        assert api.post(f'/api/onboarding/plans/{pid}/interventions/{proposed["id"]}/verify').status_code==422
        applied=api.post(f'/api/onboarding/plans/{pid}/interventions/{proposed["id"]}/applied',json={'actor':'human','evidence':'system log'}).json()
        assert applied['status']=='applied_pending_verification'
        assert api.post(f'/api/onboarding/plans/{pid}/interventions/{proposed["id"]}/verify').json()['verified']==False
        assert api.post(obs,json={'source_type':'human_input','value':90}).status_code==200
        verdict=api.post(f'/api/onboarding/plans/{pid}/interventions/{proposed["id"]}/verify').json()
        assert verdict['verdict']=='improved' and '不能证明因果' in verdict['caution']


def test_decrease_direction_comparator():
    with TestClient(create_app(':memory:')) as api:
        p=api.post('/api/onboarding/plans',json=payload(target_mode='at_most',target_value=10)).json()['plan_id']
        loop=api.post(f'/api/onboarding/plans/{p}/activate').json()['loop_id']
        api.post(f'/api/onboarding/plans/{p}/observations',json={'source_type':'human_input','value':7})
        cycle=api.post(f'/api/loops/{loop}/cycle').json()
        assert cycle['errors'][0]['error']==0 and not cycle['errors'][0]['out_of_tolerance']
        assert api.get(f'/api/onboarding/plans/{p}/status').json()['out_of_tolerance'] is False

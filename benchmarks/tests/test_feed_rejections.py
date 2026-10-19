import pytest
from benchmarks.pipeline import parse_args,rejection_indices,review_label,select_samples,submitter_plan,report_id_for


@pytest.mark.parametrize('count,percent,expected',[
    (6,0,0),(6,100,6),(6,50,3),(6,20,1),(10,20,2),(10,25,3),
    (1,1,0),(1,50,1),(4,12.5,1),
])
def test_exact_controlled_rejection_count_and_rounding(count,percent,expected):
    selected=rejection_indices(count,percent,100)
    assert len(selected)==expected
    assert selected<=set(range(count))
    assert selected==rejection_indices(count,percent,100)


def test_rejection_seed_does_not_change_selected_input():
    data=[{'language':'test','statement':str(i)} for i in range(100)]
    before=select_samples(data,20,100)
    first=rejection_indices(20,25,100)
    assert first!=rejection_indices(20,25,101)
    assert before==select_samples(data,20,100)


@pytest.mark.parametrize('proposed',['0','1'])
@pytest.mark.parametrize('truth',['0','1'])
def test_controlled_votes_overturn_or_uphold_actual_ai_label(proposed,truth):
    assert review_label(proposed,truth,True,False)==proposed
    assert review_label(proposed,truth,True,True)!=proposed
    assert review_label(proposed,truth,False,False)==truth


def test_default_retains_dataset_label_review():
    args,_=parse_args([])
    assert args.rejection_percent is None
    assert rejection_indices(100,args.rejection_percent,args.seed)==frozenset()
    assert parse_args(['--rejection-percent','0'])[0].rejection_percent==0
    assert parse_args(['--rejection-percent','12.5'])[0].rejection_percent==12.5


@pytest.mark.parametrize('value',['-1','100.1','nan','inf','-inf','text'])
def test_invalid_percent_rejected_before_deployment_access(value):
    with pytest.raises(SystemExit) as error:parse_args(['--rejection-percent',value])
    assert error.value.code==2


def test_invalid_proposed_label_cannot_be_used_for_a_vote():
    with pytest.raises(RuntimeError,match='invalid AI label'):
        review_label('invalid','0',True,True)


@pytest.mark.parametrize('orgs',[3,5,21,35])
@pytest.mark.parametrize('percent,expected',[(0,0),(25,3),(50,5),(100,10)])
def test_submitter_counts_dynamic_membership_and_seed(orgs,percent,expected):
    plan=submitter_plan(10,percent,100,orgs)
    assert len(plan)==10 and sum(org!='org1' for org in plan)==expected
    assert set(plan)<={f'org{i}' for i in range(1,orgs+1)}
    assert plan==submitter_plan(10,percent,100,orgs)


def test_random_submitters_do_not_change_inputs_or_force_overturn_positions():
    data=[{'language':'test','statement':str(i)} for i in range(100)]
    original=select_samples(data,20,100)
    plan=submitter_plan(20,50,100,5)
    assert [i for i,org in enumerate(plan) if org!='org1']!=sorted(rejection_indices(20,50,100))
    assert plan!=submitter_plan(20,50,101,5)
    assert select_samples(data,20,100)==original


def test_random_cli_default_and_decimals():
    assert parse_args([])[0].random_percent==0
    assert parse_args(['--random','12.5'])[0].random_percent==12.5


@pytest.mark.parametrize('value',['-1','101','nan','inf','invalid'])
def test_invalid_submitter_percentage_rejected(value):
    with pytest.raises(SystemExit) as error:parse_args(['--random',value])
    assert error.value.code==2


def test_report_id_uses_actual_submitter_msp():
    import hashlib
    assert report_id_for('org21','same')==hashlib.sha256(b'Org21MSP\0same').hexdigest()
    assert report_id_for('org1','same')!=report_id_for('org2','same')

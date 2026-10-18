"""Scripted majority review driven by the report's frozen ledger policy."""
from blockchain.scripts.deployment import http


def review_to_majority(path, record, keys, outcome, reasoning):
    eligible=record['eligible_reviewers'];required=record['required_votes']
    if required!=len(eligible)//2+1 or len(eligible)<2:
        raise RuntimeError('unexpected ledger review policy')
    reviewers=[org for org in sorted(keys,key=lambda org:int(org[3:])) if f'Org{org[3:]}MSP' in eligible]
    if len(reviewers)<required:raise RuntimeError('insufficient provisioned eligible reviewer credentials')
    for org in reviewers:
        if record['status']=='FINAL':break
        http(path+'/fact-check',{'outcome':outcome,'reasoning':reasoning,'support':[]},keys[org])
        record=http(path+'/chain',key=keys[org])
    if record['status']!='FINAL':raise RuntimeError('review policy did not reach a final verdict')
    return record

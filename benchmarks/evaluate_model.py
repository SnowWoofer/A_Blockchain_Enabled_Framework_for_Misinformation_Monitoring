#!/usr/bin/env python3
"""Evaluate the supplied checkpoint locally; never infer its training provenance."""
from __future__ import annotations
import argparse
import collections
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def metrics(rows):
    from sklearn.metrics import accuracy_score,precision_recall_fscore_support,confusion_matrix
    y=[r['truth'] for r in rows];p=[r['prediction'] for r in rows]
    precision,recall,f1,_=precision_recall_fscore_support(y,p,average='binary',zero_division=0)
    return dict(n=len(rows),accuracy=accuracy_score(y,p),precision=precision,recall=recall,f1=f1,
        confusion_matrix_tn_fp_fn_tp=confusion_matrix(y,p,labels=[0,1]).ravel().tolist())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--quantization',choices=['none','int8'],required=True)
    p.add_argument('--samples',type=int,default=0,help='0 evaluates every supplied test row')
    p.add_argument('--seed',type=int,default=619);p.add_argument('--batch-size',type=int,default=4)
    p.add_argument('--threads',type=int,default=2);p.add_argument('--output',required=True)
    a=p.parse_args();out=Path(a.output)
    if out.exists():p.error('output already exists')
    os.environ.update(MODEL_PATH=str(ROOT/'apps/flagging-engine/model'),MODEL_QUANTIZATION=a.quantization,
        TORCH_DEVICE='cpu',KAFKA_ENABLED='false',HF_HUB_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    sys.path.insert(0,str(ROOT/'apps/flagging-engine'))
    import torch
    torch.set_num_threads(a.threads)
    from app.model import MisinfoModel
    data_path=ROOT/'data/translated_masked_samples_test.json';data=json.loads(data_path.read_text())
    # Round-robin across language/label strata, then reproducibly shuffle.
    if a.samples and a.samples<len(data):
        rng=random.Random(a.seed);groups=collections.defaultdict(list)
        for row in data:groups[(row['language'],row['classification'])].append(row)
        for rows in groups.values():rng.shuffle(rows)
        selected=[]
        while len(selected)<a.samples:
            for key in sorted(groups):
                if groups[key] and len(selected)<a.samples:selected.append(groups[key].pop())
        data=selected;rng.shuffle(data)
    started=time.perf_counter();model=MisinfoModel();load_s=time.perf_counter()-started
    results=[];batches=[];started=time.perf_counter()
    for first in range(0,len(data),a.batch_size):
        batch=data[first:first+a.batch_size];t=time.perf_counter()
        predictions=model.predict_batch([r['statement'] for r in batch]);elapsed=time.perf_counter()-t
        batches.append(dict(first=first,count=len(batch),elapsed_s=elapsed))
        for row,pred in zip(batch,predictions):
            results.append(dict(id=row['id'],language=row['language'],truth=int(row['classification']=='misinformation'),
                prediction=int(pred['flagged']),probability=pred['misinformation_probability'],confidence=pred['confidence']))
        print(json.dumps(dict(completed=len(results),total=len(data),batch_s=elapsed)),flush=True)
    result={'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),'configuration':vars(a),
        'weights_sha256':model.weights_sha256,'model_version':model.model_version,
        'runtime_manifest':model.runtime_manifest,
        'data_sha256':hashlib.sha256(data_path.read_bytes()).hexdigest(),'supplied_file_rows':len(json.loads(data_path.read_text())),'load_s':load_s,
        'inference_s':time.perf_counter()-started,'torch_version':torch.__version__,
        'metrics':metrics(results),'by_language':{lang:metrics([r for r in results if r['language']==lang]) for lang in sorted({r['language'] for r in results})},
        'predictions':results,'batches':batches,
        'limitations':'Supplied synthetic test file; training split independence and training seed/method are not established by checkpoint contents. Subsample metrics do not reproduce the full reported test result.'}
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['metrics']))


if __name__=='__main__':main()

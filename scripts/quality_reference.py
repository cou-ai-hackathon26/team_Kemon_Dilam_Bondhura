"""Package aggregate measurements from the local evaluation runner for the owner dashboard."""
import argparse
import json
from pathlib import Path
import re


def measured(suite, metric, passed, samples, source, checksum, model=None):
    if (type(passed) is not int or type(samples) is not int or not 0<=passed<=samples
            or not re.fullmatch(r'[a-f0-9]{64}',checksum)):
        raise ValueError('Invalid aggregate evaluation evidence')
    return dict(suite=suite, metric=metric, passed=passed, samples=samples, source=source,
                dataset_sha256=checksum, model_sha256=model)


def package(smoke, candidate, training):
    rows=[]
    for suite in ('heldout_eval','heldout_eval_v2','heldout_eval_v3'):
        data=smoke['assistant'][suite]
        rows.append(measured(suite,'answer',data['overall']['passed'],data['overall']['cases'],
                             'synthetic',data['corpus_sha256']))
    model=training['model_sha256']
    if not re.fullmatch(r'[a-f0-9]{64}',model):
        raise ValueError('Invalid model digest')
    for label, report, field, artifact in [('browser_rules',smoke,'baseline',smoke['extraction']['baseline_sha256']),
                                           ('hisab_tagger',candidate,'candidate',model)]:
        dataset=report['extraction']
        if dataset['source']!='synthetic':
            raise ValueError('Only synthetic reference results can be included')
        for metric in ('amount_minor','direction','party'):
            values=dataset[field][metric]
            rows.append(measured(label, metric, values['correct'], values['total'],
                                 'synthetic',dataset['dataset_sha256'],artifact))
    return rows


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('smoke','candidate','training','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    inputs=[json.loads(path.read_text()) for path in (args.smoke,args.candidate,args.training)]
    args.output.write_text(json.dumps(package(*inputs),indent=2)+'\n')

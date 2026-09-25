#!/usr/bin/env python3
"""Require smoke verification to reject wrong computation and missing/reordered work."""
import copy
import csv
import json
from pathlib import Path
import sys
import tempfile
from configure import load
from verify_smoke import check_observations

source, output = map(Path, sys.argv[1:])
config = json.loads((source/'resolved.json').read_text())['benchmark']
with (source/'npu_requests.csv').open() as stream:
    original = list(csv.DictReader(stream))
assert check_observations(config, original)['passed']
checks = {}
for fault in ('wrong_input', 'wrong_output', 'wrong_readback', 'missing_transaction', 'extra_transaction', 'reordered_transactions'):
    rows = copy.deepcopy(original)
    if fault.startswith('wrong_'):
        index = {'wrong_input':0, 'wrong_output':4, 'wrong_readback':6}[fault]
        for row in rows[index:index+2]:
            if row['data']:
                data = bytearray.fromhex(row['data']); data[0] ^= 1; row['data'] = data.hex()
    elif fault == 'missing_transaction': rows = rows[:-2]
    elif fault == 'extra_transaction': rows += rows[-2:]
    else: rows = rows[2:4] + rows[:2] + rows[4:]
    try: check_observations(config, rows)
    except AssertionError as error: checks[fault] = {'rejected': True, 'reason': str(error)}
    else: raise AssertionError('Corrupted smoke evidence accepted: '+fault)
with tempfile.TemporaryDirectory(prefix='smoke-config-') as temporary:
    path = Path(temporary)/'benchmark.json'
    for label, replacement in [('negative',-1), ('overflow',2**32), ('boolean',True)]:
        path.write_text(json.dumps({'name':'smoke', 'input':replacement}))
        try: load(path)
        except ValueError as error: checks['config_'+label] = {'rejected':True, 'reason':str(error)}
        else: raise AssertionError('Bad smoke configuration accepted: '+label)
    for value in (0, 0xffffffff):
        path.write_text(json.dumps({'name':'smoke', 'input':value})); load(path)
    checks['config_uint32_boundaries'] = {'passed':True}
result = {'passed':True, 'checks':checks}
output.write_text(json.dumps(result,indent=2)+'\n')
print(output.read_text())

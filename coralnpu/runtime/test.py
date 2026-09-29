#!/usr/bin/env python3
"""Fresh device acceptance, with every case kept in its user project's result/ folder."""
import argparse
import copy
import datetime
import json
from pathlib import Path
import subprocess
import sys
from paths import ROOT,RUNTIME,CACHE,MEMSIM_BUILD,STORAGE_ROOT,relative,dump
from report import write_status
from portable import sanitize
p=argparse.ArgumentParser();p.add_argument('--project',choices=['smoke','llm']);o=p.parse_args()
stamp='test-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
# Full-suite bookkeeping is an internal maintenance artifact, never a user configuration.
out=CACHE/'validation'/stamp;out.mkdir(parents=True);write_status(out,'running')
def read(p):return json.loads(p.read_text())
def invoke(cmd,log):
    with (out/log).open('w') as stream:subprocess.run(list(map(str,cmd)),cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,check=True)
cases={};directories={}
def case(project,name,changes):
    base=ROOT/'user'/project
    directory=base/'result'/stamp/name
    config=base/'result'/stamp/'configs'/(name+'.json');config.parent.mkdir(parents=True,exist_ok=True)
    c=read(base/'config.json')
    for group,fields in changes.items():c[group].update(fields)
    dump(config,c)
    print('Running '+project+'/'+name,flush=True)
    invoke(['bash','user/run.sh',project,'--config',relative(config,base),'--output',relative(directory,base)],name+'.log')
    result=read(directory/'summary.json');assert result['passed'];cases[name]=result;directories[name]=directory
try:
    invoke([sys.executable,relative(RUNTIME/'check.py')],'environment.log')
    plan=json.loads(subprocess.check_output(['ctest','--test-dir',relative(MEMSIM_BUILD),'--show-only=json-v1'],cwd=ROOT,text=True))
    invoke(['ctest','--test-dir',relative(MEMSIM_BUILD),'--output-on-failure'],'native.log')
    invoke([sys.executable,relative(STORAGE_ROOT/'mem_sim/integration/check_online.py'),relative(MEMSIM_BUILD/'libstoragestacked_memsim.so'),relative(out/'api')],'api.log')
    if o.project in (None,'smoke'):
        case('smoke','smoke',{})
        case('smoke','smoke_slow',{'memsim':{'scale':4}})
        for script in ('check_smoke_negative','check_negative'):
            invoke([sys.executable,relative(RUNTIME/(script+'.py')),relative(directories['smoke']),relative(out/(script+'.json'))],script+'.log')
        fast,slow=cases['smoke'],cases['smoke_slow']
        assert fast['smoke']['output']==slow['smoke']['output']
        assert fast['devices']['cycles']<slow['devices']['cycles']
        assert fast['sources']['coralnpu']['roundtrip_mean_ns']<slow['sources']['coralnpu']['roundtrip_mean_ns']
        assert read(directories['smoke_slow']/'memsim_config.json')['period_fs']==4*read(directories['smoke']/'memsim_config.json')['period_fs']
    if o.project in (None,'llm'):
        for name,changes in [
            ('llm',{}),('llm_slow',{'memsim':{'scale':4}}),('llm_replay',{'axi':{'replay':True}}),
            ('llm_backpressure',{'npu':{'clock_mhz':250},'axi':{'period_ns':3,'outstanding':1},'memsim':{'queue':1,'slots':1,'response_hold':7}}),
            ('llm_no_cache',{'program':{'kv_cache':False}}),('llm_long',{'program':{'prompt':'one two'}})]:
            case('llm',name,changes)
        invoke([sys.executable,relative(RUNTIME/'check_llm_negative.py'),relative(directories['llm']),relative(out/'check_llm_negative.json')],'llm-negative.log')
        a,b,c=(cases[k] for k in ('llm','llm_slow','llm_no_cache'))
        for name in ('llm_slow','llm_replay','llm_backpressure','llm_no_cache'):
            assert cases[name]['llm']['generated_token_ids']==a['llm']['generated_token_ids']
        assert b['devices']['cycles']>a['devices']['cycles'] and c['devices']['cycles']>a['devices']['cycles']
        assert b['llm']['timing']['inference_to_last_token_ns']>a['llm']['timing']['inference_to_last_token_ns']
        assert b['sources']['coralnpu']['transactions']==a['sources']['coralnpu']['transactions']
        assert read(directories['llm_slow']/'memsim_config.json')['period_fs']==4*read(directories['llm']/'memsim_config.json')['period_fs']
        assert c['llm']['forward_calls']>a['llm']['forward_calls']
        assert c['llm']['traffic']['weights']['reads']>a['llm']['traffic']['weights']['reads']
    negatives={p.stem:read(p) for p in out.glob('check*negative.json')};assert all(v['passed'] for v in negatives.values())
    result={'passed':True,'native_tests_passed':len(plan['tests']),'api_check':read(out/'api/api_check.json'),'negative_checks':negatives,
            'cases':{name:{'passed':True,'cycles':s['devices']['cycles'],'transactions':s['sources']['coralnpu']['transactions'],
                           'output':s['smoke']['output'] if 'smoke' in s else s['llm']['generated_text'],
                           'report':relative(directories[name]/'report.md',ROOT)} for name,s in cases.items()}}
    dump(out/'summary.json',result)
    lines=['# 回归报告','',f"PASS：{len(cases)} 个设备场景、{len(plan['tests'])} 项原生测试、在线 C ABI 和错误拒绝检查通过。",'',
           '| 场景 | 输出 | NPU 周期 | 外部事务 | 报告 |','|---|---|---:|---:|---|']
    for name,s in result['cases'].items():lines.append(f"| {name} | {s['output']} | {s['cycles']} | {s['transactions']} | [查看]({relative(directories[name]/'report.md',out)}) |")
    (out/'report.md').write_text('\n'.join(lines)+'\n')
    print('PASS: '+relative(out/'report.md'))
except Exception as error:
    write_status(out,'acceptance',error);raise
finally:sanitize(out)

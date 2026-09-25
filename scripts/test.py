#!/usr/bin/env python3
"""Resumable acceptance; runs independent cases concurrently under build locks."""
from storage_dependency import STORAGE_ROOT, MEMSIM_BUILD, storage_version
import argparse, datetime, json, os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from configure import ROOT, DEVICE, load, build_inputs
p = argparse.ArgumentParser()
p.add_argument('--output', type=Path)
p.add_argument('--resume', action='store_true', help='Reuse passed cases with matching configuration, source versions and binary sizes')
a = p.parse_args()
out = (a.output or ROOT / 'results' / ('acceptance-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))).resolve()
out.mkdir(parents=True, exist_ok=a.resume)
(out / 'summary.json').write_text('{"passed":false,"stage":"running"}\n')

def read(path):
    return json.loads(path.read_text())

def invoke(cmd, filename):
    with (out / filename).open('w') as stream:
        subprocess.run(list(map(str, cmd)), cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True)

def reusable(name, flags):
    directory = out / name
    if not a.resume or not (directory / 'summary.json').exists():
        return False
    if not read(directory / 'summary.json')['passed']:
        return False
    bench = flags[flags.index('--benchmark') + 1] if '--benchmark' in flags else 'config/benchmark.json'
    arch_path = flags[flags.index('--architecture') + 1] if '--architecture' in flags else 'config/architecture.json'
    arch, b = load(bench, arch_path)
    if '--scale' in flags:
        arch['memory']['scale'] = int(flags[flags.index('--scale') + 1])
    if '--replay' in flags:
        arch['axi']['replay'] = True
    saved = read(directory / 'resolved.json')
    env = read(directory / 'environment.json')
    # The one staged ELF is deliberately replaced when switching benchmark presets.
    # Check it against this case only while that same preset is staged; every
    # simulator/shared library must still match the recorded artifact size.
    staged = ROOT / 'build/device-config.json'
    same_kernel = staged.exists() and read(staged) == build_inputs(arch, b)
    files = [f for f in env['files'] if not f['name'].endswith('.elf') or same_kernel]
    model_matches = b['name'] != 'tiny_llm' or saved.get('llm_model') == build_inputs(arch, b)['model']
    return model_matches and saved['architecture'] == arch and saved['benchmark'] == b and (env['sources'] == read(ROOT / 'config/sources.json')) and env.get('storage') == storage_version() and all(((ROOT / f['name']).is_file() and (ROOT / f['name']).stat().st_size == f['bytes'] for f in files))

def run_case(case):
    name, flags = case
    if reusable(name, flags):
        print('Reuse verified ' + DEVICE + ' ' + name, flush=True)
        return (name, read(out / name / 'summary.json'), True)
    if (out / name).exists():
        raise RuntimeError('Existing case is incomplete or no longer matches; use a new --output directory: ' + str(out / name))
    print('Running ' + DEVICE + ' ' + name, flush=True)
    invoke([sys.executable, ROOT / 'scripts/run.py', '--output', out / name, *flags], name + '.log')
    result = read(out / name / 'summary.json')
    assert result['passed']
    return (name, result, False)
try:
    invoke([sys.executable, ROOT / 'scripts/check.py'], 'environment-check.log')
    plan = json.loads(subprocess.check_output(['ctest', '--test-dir', str(MEMSIM_BUILD), '--show-only=json-v1'], text=True))
    count = len(plan['tests'])
    native = out / 'native-tests.log'
    if not (a.resume and native.exists() and re.search(f'100% tests passed, 0 tests failed out of {count}\\b', native.read_text())):
        invoke(['ctest', '--test-dir', MEMSIM_BUILD, '--output-on-failure'], 'native-tests.log')
    if not (a.resume and (out / 'api/api_check.json').exists() and read(out / 'api/api_check.json')['passed']):
        invoke([sys.executable, STORAGE_ROOT / 'mem_sim/integration/check_online.py', MEMSIM_BUILD / 'libstoragestacked_memsim.so', out / 'api'], 'api.log')
    variant = 'strided'
    cases = {}
    reused = []
    for case in [('smoke', ['--benchmark', 'config/benchmarks/smoke.json']),
                 ('smoke_slow', ['--benchmark', 'config/benchmarks/smoke.json', '--scale', '4'])]:
        name, result, was_reused = run_case(case)
        cases[name] = result
        if was_reused: reused.append(name)
    invoke([sys.executable, ROOT / 'scripts/check_smoke_negative.py', out / 'smoke', out / 'smoke-negative.json'], 'smoke-negative.log')
    assert cases['smoke']['smoke']['output'] == cases['smoke_slow']['smoke']['output']
    assert cases['smoke_slow']['devices']['cycles'] > cases['smoke']['devices']['cycles']
    assert cases['smoke_slow']['sources'][DEVICE]['roundtrip_mean_ns'] > cases['smoke']['sources'][DEVICE]['roundtrip_mean_ns']
    assert read(out / 'smoke_slow/memsim_config.json')['period_fs'] == 4 * read(out / 'smoke/memsim_config.json')['period_fs']
    group = [('default', []), ('slow', ['--scale', '4']), ('replay', ['--replay']), ('backpressure', ['--architecture', 'config/architectures/backpressure.json'])]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, result, was_reused in pool.map(run_case, group):
            cases[name] = result
            if was_reused:
                reused.append(name)
    extra = ('alternate', ['--benchmark', 'config/benchmarks/' + variant + '.json'])
    name, result, was_reused = run_case(extra)
    cases[name] = result
    if was_reused:
        reused.append(name)
    invoke([sys.executable, ROOT / 'scripts/check_negative.py', out / 'default', out / 'negative.json'], 'negative.log')
    fast, slow = (cases['default'], cases['slow'])
    assert read(out / 'slow/memsim_config.json')['period_fs'] == 4 * read(out / 'default/memsim_config.json')['period_fs']
    assert slow['devices']['cycles'] > fast['devices']['cycles']
    assert slow['completion']['tick_fs'] > fast['completion']['tick_fs']
    assert slow['sources'][DEVICE]['roundtrip_mean_ns'] > fast['sources'][DEVICE]['roundtrip_mean_ns']
    assert slow['sources'][DEVICE]['transactions'] == fast['sources'][DEVICE]['transactions']
    llm_flags = ['--benchmark', 'config/benchmarks/tiny_llm.json']
    llm_group = [('llm', llm_flags), ('llm_slow', llm_flags + ['--scale', '4']),
                 ('llm_replay', llm_flags + ['--replay']),
                 ('llm_backpressure', llm_flags + ['--architecture', 'config/architectures/backpressure.json'])]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, result, was_reused in pool.map(run_case, llm_group):
            cases[name] = result
            if was_reused: reused.append(name)
    for case in [('llm_no_cache', ['--benchmark', 'config/benchmarks/tiny_llm_no_cache.json']),
                 ('llm_long', ['--benchmark', 'config/benchmarks/tiny_llm_long.json'])]:
        name, result, was_reused = run_case(case)
        cases[name] = result
        if was_reused: reused.append(name)
    invoke([sys.executable, ROOT / 'scripts/check_llm_negative.py', out / 'llm', out / 'llm-negative.json'], 'llm-negative.log')
    cached, uncached, llm_slow = (cases[k] for k in ('llm', 'llm_no_cache', 'llm_slow'))
    for name in ('llm_slow', 'llm_replay', 'llm_backpressure', 'llm_no_cache'):
        assert cases[name]['llm']['generated_token_ids'] == cached['llm']['generated_token_ids']
    assert llm_slow['devices']['cycles'] > cached['devices']['cycles']
    assert llm_slow['llm']['timing']['inference_to_last_token_ns'] > cached['llm']['timing']['inference_to_last_token_ns']
    assert llm_slow['sources'][DEVICE]['transactions'] == cached['sources'][DEVICE]['transactions']
    assert read(out / 'llm_slow/memsim_config.json')['period_fs'] == 4 * read(out / 'llm/memsim_config.json')['period_fs']
    assert uncached['devices']['cycles'] > cached['devices']['cycles']
    assert uncached['llm']['forward_calls'] > cached['llm']['forward_calls']
    assert uncached['llm']['traffic']['weights']['reads'] > cached['llm']['traffic']['weights']['reads']
    result = {'passed': True, 'device': DEVICE, 'native_tests_passed': count, 'api_check': read(out / 'api/api_check.json'), 'negative_checks': read(out / 'negative.json'), 'cases': {k: {'passed': v['passed'], 'sources': v['sources'], 'devices': v['devices']} for k, v in cases.items()}, 'reused_verified_cases': reused, 'memory_feedback': {'passed': True, 'scale': 4, 'cycles': [fast['devices']['cycles'], slow['devices']['cycles']]}}
    assert result['api_check']['passed']
    result['smoke'] = {'passed': True, 'cases': {k: cases[k]['smoke'] for k in ('smoke', 'smoke_slow')},
                       'negative_checks': read(out / 'smoke-negative.json'),
                       'memory_feedback_cycles': [cases[k]['devices']['cycles'] for k in ('smoke', 'smoke_slow')]}
    result['llm'] = {'passed': True, 'cases': {k: v['llm'] for k, v in cases.items() if 'llm' in v},
                     'negative_checks': read(out / 'llm-negative.json'),
                     'memory_feedback_cycles': [cached['devices']['cycles'], llm_slow['devices']['cycles']],
                     'kv_cache_cycles': [cached['devices']['cycles'], uncached['devices']['cycles']]}
    (out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print('PASS: ' + str(out / 'summary.json'))
except Exception as error:
    (out / 'summary.json').write_text(json.dumps({'passed': False, 'error': str(error)}, indent=2) + '\n')
    raise

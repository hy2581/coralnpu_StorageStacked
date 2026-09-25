#!/usr/bin/env python3
"""Run one real accelerator benchmark and require end-to-end acceptance."""
from storage_dependency import STORAGE_ROOT, MEMSIM_BUILD, storage_version, require_build
import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
from configure import ROOT, DEVICE, load, build_inputs, kernel_path

def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')

def main():
    require_build()
    p = argparse.ArgumentParser()
    p.add_argument('--architecture', default='config/architecture.json')
    p.add_argument('--benchmark', default='config/benchmark.json')
    p.add_argument('--output', type=Path)
    p.add_argument('--scale', type=int)
    p.add_argument('--replay', action='store_true')
    o = p.parse_args()
    a, b = load(o.benchmark, o.architecture)
    if o.scale is not None:
        if not 0 < o.scale <= 1024:
            raise ValueError('scale must be in [1,1024]')
        a['memory']['scale'] = o.scale
    if o.replay:
        a['axi']['replay'] = True
    output = o.output or ROOT / 'results' / datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    dump(output / 'summary.json', {'passed': False, 'stage': 'preflight', 'device': DEVICE})
    lock = (ROOT / 'build/build.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_SH)
    env = dict(os.environ)
    extra = [MEMSIM_BUILD, Path(env['SS_PREFIX']) / 'lib']
    extra += [Path(env['SS_DEPS_ROOT']) / 'xpu-native/lib']
    env['LD_LIBRARY_PATH'] = ':'.join(map(str, extra))

    def run(command, log):
        with (output / log).open('a') as stream:
            subprocess.run(list(map(str, command)), cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, check=True)
    stage = 'build benchmark'
    try:
        built = ROOT / 'build/device-config.json'
        if not built.exists() or json.loads(built.read_text()) != build_inputs(a, b):
            fcntl.flock(lock, fcntl.LOCK_UN)
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not built.exists() or json.loads(built.read_text()) != build_inputs(a, b):
                run(['bash', ROOT / 'scripts/build_device.sh', o.benchmark, '--kernel-only'], 'benchmark-build.log')
            fcntl.flock(lock, fcntl.LOCK_SH)
        simulator = ROOT / 'build/native/coralnpu_sim'
        c = {'device': DEVICE, 'architecture': a, 'benchmark': b}
        c.update(library=str(ROOT / 'build/coralnpu/libcoralnpu-native.so'), kernel=str(kernel_path(b)))
        if b['name'] == 'tiny_llm':
            from llm_model import load_model
            c['llm_model'] = load_model(b['model'])
        files = [simulator, MEMSIM_BUILD / 'libstoragestacked_memsim.so', Path(c['library']), Path(c['kernel'])]
        for file in files:
            if not file.is_file():
                raise FileNotFoundError(str(file) + '; run ./run.sh build')
        dump(output / 'resolved.json', c)
        manifest = {'storage': storage_version(), 'sources': json.loads((ROOT / 'config/sources.json').read_text()), 'files': [{'name': str(x.relative_to(ROOT)), 'bytes': x.stat().st_size} for x in files], 'python': sys.version, 'compiler': subprocess.check_output([env['AXI_CXX'], '--version'], text=True).splitlines()[0]}
        dump(output / 'environment.json', manifest)
        stage = 'simulate'
        dump(output / 'summary.json', {'passed': False, 'stage': stage, 'device': DEVICE})
        run([simulator, output / 'resolved.json', output], 'run.log')
        stage = 'verify data and link'
        dump(output / 'summary.json', {'passed': False, 'stage': stage, 'device': DEVICE})
        run([sys.executable, ROOT / 'scripts/validate.py', output], 'validation-run.log')
        if b['name'] == 'smoke':
            result = json.loads((output / 'summary.json').read_text())
            smoke = result['smoke']
            print(f"SMOKE: {smoke['input']} + 1 = {smoke['output']} (uint32); "
                  f"{smoke['external_transactions']} external transactions; "
                  f"{result['checks']['check_summary']['axi_handshakes']} AXI handshakes; "
                  f"{result['devices']['cycles']} NPU cycles")
            print('Report: ' + str(output / 'smoke_report.md'))
        print('PASS: ' + str(output / 'summary.json'))
    except Exception as error:
        dump(output / 'summary.json', {'passed': False, 'stage': stage, 'device': DEVICE, 'error': str(error)})
        print(f'FAIL ({stage}): {output}', file=sys.stderr)
        raise
if __name__ == '__main__':
    main()

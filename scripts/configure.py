#!/usr/bin/env python3
"""Validate public configuration, generate the kernel and record build inputs."""
import argparse
import json
from pathlib import Path
import shutil
ROOT = Path(__file__).resolve().parents[1]
DEVICE = 'coralnpu'

def read(path):
    p = Path(path)
    return json.loads((p if p.is_absolute() else ROOT / p).read_text())

def positive(value, name, upper=1000000):
    if type(value) is not int or not 0 < value <= upper:
        raise ValueError(f'{name} must be an integer in [1, {upper}]')

def keys(obj, expected, name):
    if set(obj) != set(expected.split()):
        raise ValueError(f"{name}: expected keys {expected}; got {', '.join(obj)}")

def load(benchmark='config/benchmark.json', architecture='config/architecture.json'):
    a, b = (read(architecture), read(benchmark))
    keys(a, 'device_clock_mhz axi memory max_ticks' + '', 'architecture')
    positive(a['device_clock_mhz'], 'device_clock_mhz', 10000)
    if 10 ** 9 % a['device_clock_mhz']:
        raise ValueError('device clock must have an integral fs period')
    positive(a['max_ticks'], 'max_ticks', 10 ** 17)
    keys(a['axi'], 'period_ns outstanding planes stalls replay', 'axi')
    for k in ('period_ns', 'outstanding', 'planes'):
        positive(a['axi'][k], 'axi.' + k, {'period_ns': 1000, 'outstanding': 128, 'planes': 4}[k])
    for k in ('stalls', 'replay'):
        if type(a['axi'][k]) is not bool:
            raise ValueError(k + ' must be true/false')
    keys(a['memory'], 'standard channels scale queue slots response_hold', 'memory')
    if a['memory']['standard'] not in ('hbm3', 'hbm4', 'lpddr5', 'lpddr6'):
        raise ValueError('unsupported memory standard')
    for k in ('channels', 'scale', 'queue', 'slots'):
        positive(a['memory'][k], 'memory.' + k, 1024)
    if type(a['memory']['response_hold']) is not int or a['memory']['response_hold'] < 0:
        raise ValueError('response_hold must be a nonnegative integer')
    if b.get('name') == 'tiny_llm':
        from llm_model import load_model, encode
        keys(b, 'name model prompt generated_tokens kv_cache', 'benchmark')
        model_path = Path(b['model'])
        model_path = (model_path if model_path.is_absolute() else ROOT / model_path).resolve()
        try:
            model_path.relative_to(ROOT / 'config/llm')
        except ValueError:
            raise ValueError('LLM models must be stored inside this project config/llm directory')
        model = load_model(model_path)
        positive(b['generated_tokens'], 'generated_tokens', 8)
        if type(b['kv_cache']) is not bool:
            raise ValueError('kv_cache must be true/false')
        if len(encode(model, b['prompt'])) + b['generated_tokens'] - 1 > model['architecture']['context_length']:
            raise ValueError('prompt plus processed decode tokens exceeds model context_length')
    elif b.get('name') == 'smoke':
        keys(b, 'name input', 'benchmark')
        if type(b['input']) is not int or not 0 <= b['input'] <= 0xffffffff:
            raise ValueError('smoke input must be a uint32 integer')
    elif b.get('name') == 'memory_roundtrip':
        keys(b, 'name words iterations stride_words seed multiplier', 'benchmark')
        for k in ('words', 'iterations', 'stride_words', 'multiplier'):
            positive(b[k], k, 16384)
        if type(b['seed']) is not int or not 0 <= b['seed'] <= 65535:
            raise ValueError('seed must be in [0,65535]')
        if b['words'] * b['stride_words'] > 16384:
            raise ValueError('strided input and output buffers would overlap')
        if b['words'] * b['iterations'] > 1000000:
            raise ValueError('benchmark too large for bounded acceptance')
    else:
        raise ValueError('unknown benchmark')
    required_map = {'shared_buffer': (2415919104, 268435456), 'npu_work': (2952790016, 268435456), 'npu_mailbox': (3221225472, 16)}
    actual_map = {v['name']:(int(v['base'],16),int(v['size'],16)) for v in read('config/addrmap.json')['regions']}
    if any(actual_map.get(k) != v for k,v in required_map.items()):
        raise ValueError('addrmap differs from the compiled driver/kernel ABI; update source and rebuild together')
    return (a, b)

def build_inputs(a, b):
    result = {'device': DEVICE, 'runtime': 'native-v3-benchmarks', 'benchmark': b}
    if b['name'] == 'tiny_llm':
        from llm_model import load_model
        result['model'] = load_model(b['model'])
        result['model_tensor_order'] = list(result['model']['tensors'])
    return result

def kernel_path(b):
    return ROOT / 'build/coralnpu' / (b['name'] + '.elf')

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--benchmark-name')
    p.add_argument('--generate-kernel')
    p.add_argument('--record-build')
    o = p.parse_args()
    path = o.benchmark_name or o.generate_kernel or o.record_build or 'config/benchmark.json'
    a, b = load(path)
    if o.benchmark_name:
        print(b['name'])
    if o.generate_kernel:
        target = ROOT / 'third_party/coralnpu/native'
        for name, staged in (('memory_roundtrip', 'ddr_touch'), ('tiny_llm', 'tiny_llm'), ('smoke', 'smoke')):
            shutil.copy2(ROOT / 'benchmarks' / name / 'kernel.cc', target / (staged + '.cc'))
        if b['name'] == 'smoke':
            (target / 'smoke_config.h').write_text(
                '// Generated from config/benchmarks/smoke.json or the selected preset.\n'
                f'#define SMOKE_INPUT {b["input"]}u\n')
            return
        if b['name'] == 'tiny_llm':
            from llm_model import load_model, generate_header
            generate_header(load_model(b['model']), b, target / 'llm_config.h')
            return
        header = '// Generated from the selected config/ benchmark.\n'
        for key, macro in (('words', 'WORDS'), ('iterations', 'ITERATIONS'), ('stride_words', 'STRIDE'), ('seed', 'SEED'), ('multiplier', 'MULTIPLIER')):
            header += f'#define BENCH_{macro} {b[key]}u\n'
        (target / 'benchmark_config.h').write_text(header)
    if o.record_build:
        (ROOT / 'build/device-config.json').write_text(json.dumps(build_inputs(a, b), indent=2) + '\n')
if __name__ == '__main__':
    main()

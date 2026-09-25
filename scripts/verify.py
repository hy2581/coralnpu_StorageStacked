#!/usr/bin/env python3
"""Independent transaction, source, byte, compute and waveform acceptance."""
import csv
import json
from pathlib import Path
import re
import sys

def read(p):
    return json.loads(p.read_text())

def verify(root):
    c = read(root / 'resolved.json')
    dev = c['device']
    a = c['architecture']
    b = c['benchmark']
    completion = read(root / 'completion.json')
    assert completion['passed'] and completion['code'] == 0
    memory_config = read(root / 'memsim_config.json')
    assert memory_config['standard'] == a['memory']['standard']
    assert memory_config['channels'] == a['memory']['channels']
    assert memory_config['timing_scale'] == a['memory']['scale']
    assert memory_config['queue_depth'] == a['memory']['queue']
    actual_system = read(root / 'config.json')
    assert actual_system['runtime'] == 'standalone-systemc'
    assert actual_system['device_period_fs'] == 10**9 // a['device_clock_mhz']
    assert 'cpu' not in actual_system
    actual_axi = actual_system['axi']
    assert actual_axi['period'] == a['axi']['period_ns'] * 1000000
    assert actual_axi['planes'] == a['axi']['planes']
    assert actual_axi['outstanding'] == a['axi']['outstanding']
    assert actual_axi['replay'] == a['axi']['replay']
    assert actual_axi['memsim_slots'] == a['memory']['slots']
    checks = {k: read(root / (k + '.json')) for k in ('check_summary', 'aou_check_summary', 'memsim_check', 'memsim_core')}
    assert all((v['passed'] for v in checks.values()))
    wave = read(root / 'wave_audit/summary.json')
    assert len(wave) == 1 and all((v['passed'] for v in wave.values()))
    def rows(name):
        with (root / name).open() as stream:
            return list(csv.DictReader(stream))
    tx = rows('transactions.csv')
    source = rows('npu_requests.csv')
    requests = {int(r['sequence']): r for r in source if r['event'] == 'request'}
    responses = {int(r['sequence']): r for r in source if r['event'] == 'response'}
    assert len(source) == 2 * len(requests) == 2 * len(responses) == 2 * len(tx)
    assert requests.keys() == responses.keys() == {int(t['substream']) for t in tx}
    events = rows('axi_events.csv')
    aw = [r for r in events if r['channel'] == 'AW']
    ws = [r for r in events if r['channel'] == 'W']
    assert len(aw) == len(ws)
    writes_by_key = {(r['id'], int(r['tick'])): w for r, w in zip(aw, ws)}
    channels = {ch: {} for ch in ('AW', 'AR', 'B', 'R')}
    for e in events:
        if e['channel'] in channels:
            channels[e['channel']].setdefault(e['id'], []).append(e)
    latency = []
    for t in tx:
        seq = int(t['substream']); req, rsp = requests[seq], responses[seq]
        assert all(req[k] == rsp[k] for k in ('command', 'address', 'native_id', 'wire_id', 'mask'))
        assert req['wire_id'] == t['id'] and req['command'] == t['command'] and req['address'] == t['address']
        assert int(req['native_id']) == int(t['stream'])
        begin, end = int(req['tick']), int(rsp['tick'])
        assert begin == int(t['begin_tick']) and end == int(t['end_resp_tick']) and end > begin
        assert end <= completion['tick_fs'] and int(rsp['response']) == 0 and int(t['status']) == 1
        lane = int(req['address']) % 32
        ch = 'AW' if req['command'] == 'W' else 'AR'
        address = [e for e in channels[ch][t['id']] if begin < int(e['tick']) <= int(t['axi_done_tick'])]
        assert len(address) == 1 and address[0]['address'] == req['address']
        ch = 'B' if req['command'] == 'W' else 'R'
        reply = [e for e in channels[ch][t['id']] if int(e['tick']) == int(t['axi_done_tick'])]
        assert len(reply) == 1 and int(reply[0]['resp']) == 0
        if req['command'] == 'W':
            w = writes_by_key[t['id'], int(address[0]['tick'])]
            assert int(w['strb']) == int(req['mask']) << lane
            assert int(w['data']).to_bytes(32, 'little')[lane:lane+16] == bytes.fromhex(req['data'])
            assert rsp['data'] == req['data']
        else:
            assert int(reply[0]['data']).to_bytes(32, 'little')[lane:lane+16] == bytes.fromhex(rsp['data'])
        latency.append(end - begin)
    protocol = read(root / 'protocol_summary.json')
    assert latency and protocol['max_outstanding'] <= a['axi']['outstanding']
    if a['axi']['outstanding'] == 1:
        assert protocol['max_outstanding'] == 1 and protocol['capacity_denials'] > 0
    keys = {(r['command'], r['native_id']) for r in requests.values()}
    for key in keys:
        expected_order = [r['sequence'] for r in source if r['event'] == 'request' and (r['command'], r['native_id']) == key]
        actual_order = [r['sequence'] for r in source if r['event'] == 'response' and (r['command'], r['native_id']) == key]
        assert expected_order == actual_order
    sources = {dev: {'transactions': len(tx), 'bytes': 16 * len(tx),
                     'roundtrip_mean_ns': sum(latency) / len(latency) / 1000000.0}}
    log = (root / 'run.log').read_text()
    devices = {}
    assert completion['mailbox'] == 1611464704
    m = re.search('kernel finished after (\\d+) cycles', log)
    assert m
    devices['cycles'] = int(m[1])
    assert devices['cycles'] == completion['cycles']
    if b['name'] == 'tiny_llm':
        from verify_llm import verify_llm
        llm = verify_llm(root, c)
        devices.update(prompt_tokens=len(llm['prompt_tokens']), generated_tokens=len(llm['generated_token_ids']), mailbox=completion['mailbox'])
    elif b['name'] == 'smoke':
        from verify_smoke import verify_smoke
        smoke = verify_smoke(root, c)
        devices.update(input=smoke['input'], output=smoke['output'], mailbox=completion['mailbox'])
    else:
        assert b['name'] == 'memory_roundtrip', 'Unknown benchmark in saved evidence'
        n = b['words'] * b['iterations']
        assert sum(t['command'] == 'R' for t in tx) == 2*n
        assert sum(t['command'] == 'W' for t in tx) == 2*n
        assert sources[dev]['transactions'] == 4 * n
        # Coral's native AXI beat is 16 bytes even for a scalar word operation.
        # WSTRB selects the written word; read replies include the entire aligned beat.
        writes, reads = {}, {}
        stride = 4 * b['stride_words']
        def expected(address, repeat):
            base = 0x90000000 if address < 0x90010000 else 0x90010000
            offset = address - base
            if offset < 0 or offset % stride or offset // stride >= b['words']:
                return 0
            index = offset // stride
            value = 0x1000 * (index + 1) + (index ^ b['seed']) + repeat
            if base == 0x90010000: value = value * b['multiplier'] + 1
            return value & 0xffffffff
        with (root / 'memsim_bridge.csv').open() as stream:
            for row in csv.DictReader(stream):
                address = int(row['address'])
                if row['command'] == 'W' and row['event'] == 'accept':
                    data = bytes.fromhex(row['data'])
                    enabled = [i for i,v in enumerate(row['mask']) if v == '1']
                    assert len(data) == 16 and len(enabled) == 4
                    lane = enabled[0]
                    assert lane % 4 == 0 and enabled == list(range(lane,lane+4))
                    word = address + lane
                    base = 0x90000000 if word < 0x90010000 else 0x90010000
                    assert (word-base) % stride == 0 and 0 <= (word-base)//stride < b['words']
                    repeat = writes.get(word,0)
                    assert repeat < b['iterations']
                    assert int.from_bytes(data[lane:lane+4],'little') == expected(word,repeat)
                    writes[word] = repeat + 1
                if row['command'] == 'R' and row['event'] == 'return':
                    data = bytes.fromhex(row['data'])
                    assert len(data) == 16 and address % 16 == 0
                    base = 0x90000000 if address < 0x90010000 else 0x90010000
                    valid = sum(0 <= address+i-base < b['words']*stride and (address+i-base)%stride == 0 for i in range(0,16,4))
                    assert valid > 0
                    seen = reads.get(address,0)
                    repeat = seen // valid
                    assert repeat < b['iterations']
                    for lane in range(0,16,4):
                        assert int.from_bytes(data[lane:lane+4],'little') == expected(address+lane,repeat)
                    reads[address] = seen + 1
        assert len(writes) == 2*b['words'] and all(v == b['iterations'] for v in writes.values())
        assert sum(reads.values()) == 2*b['words']*b['iterations']
        devices.update(words=b['words'], iterations=b['iterations'], mailbox=completion['mailbox'])
    result = {'passed': True, 'device': dev, 'benchmark': b, 'axi_data_bits': read(root / 'protocol_summary.json')['axi_data_bits'], 'completion': completion, 'sources': sources, 'devices': devices, 'checks': checks, 'wave': wave, 'scope': 'CoralNPU RTL callbacks -> bounded AXI256 adapter -> AXI2Flit -> UCIe -> online mem_sim -> returned data'}
    if b['name'] == 'tiny_llm':
        result['llm'] = llm
    if b['name'] == 'smoke':
        result['smoke'] = smoke
    assert result['axi_data_bits'] == 256
    (root / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': True, 'device': dev, 'sources': sources, 'devices': devices}, indent=2))
if __name__ == '__main__':
    verify(Path(sys.argv[1]))

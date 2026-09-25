#!/usr/bin/env python3
"""Availability/isolation only; run/test verifies computation and the online link."""
from storage_dependency import STORAGE_ROOT, MEMSIM_BUILD, storage_version, require_build
import ctypes,json,os,re,subprocess
from pathlib import Path
from configure import ROOT,DEVICE,load
require_build()
load()
for legacy in ('third_party/gem5', 'gem5_axi', 'integration/dev',
               'integration/hettrace', 'integration/coralnpuint',
               'third_party/coralnpu/gem5int', 'scripts/simulate.py',
               'build/retired-gem5', 'build/coralnpu/libcoralnpu-gem5.so'):
    assert not (ROOT / legacy).exists(), 'Retired driver is still present: ' + legacy
simulator=ROOT/'build/native/coralnpu_sim'
lib=ROOT/'build/coralnpu/libcoralnpu-native.so'
memory=MEMSIM_BUILD/'libstoragestacked_memsim.so'
subprocess.run([str(simulator),'--check'],check=True)
ctypes.CDLL(str(memory))
ctypes.CDLL(str(lib))
linked='\n'.join(subprocess.check_output(['ldd',str(p)],text=True) for p in (simulator,lib,memory))
assert 'not found' not in linked and 'gem5' not in linked
allowed=(ROOT,Path(os.environ['SS_DEPS_ROOT']).resolve(),Path('/lib'),Path('/lib64'),Path('/usr'))
for dependency in re.findall(r'(/[\w./+-]+)',linked):
    assert any(Path(dependency).is_relative_to(p) for p in allowed),dependency
for artifact in (simulator,lib,memory):
    symbols=subprocess.check_output(['nm','-C',str(artifact)],text=True)
    assert 'gem5::' not in symbols and 'coralnpu_gem5' not in symbols, str(artifact)
    if artifact != simulator:
        assert 'sc_core::' not in symbols, 'Second SystemC kernel: ' + str(artifact)
assert 'libsystemc' not in linked  # the single standalone kernel is statically linked
print(json.dumps({'available':True,'device':DEVICE,'runtime':'standalone-systemc',
 'simulator_bytes':simulator.stat().st_size,'library_bytes':lib.stat().st_size,
 'scope':'availability and device isolation'},indent=2))

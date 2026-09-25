"""Locate the independent storage source; never fetch or vendor it implicitly."""
import json, os, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
STORAGE_ROOT = Path(os.environ.get('STORAGE_STACK_ROOT', ROOT.parent / 'axi_StorageStacked')).resolve()
MEMSIM_BUILD = ROOT / 'build/memsim'
if not (STORAGE_ROOT / 'storage_axi/storage_config.hh').is_file():
    raise RuntimeError('Clone axi_StorageStacked next to this driver or set STORAGE_STACK_ROOT')
expected = json.loads((ROOT / 'config/storage_dependency.json').read_text())
if (STORAGE_ROOT / 'VERSION').read_text().strip() != expected['version']:
    raise RuntimeError('Incompatible storage project version')
def storage_version():
    result = {'root': str(STORAGE_ROOT), 'version': expected['version']}
    if (STORAGE_ROOT / '.git').exists():
        result['commit'] = subprocess.check_output(['git', '-C', str(STORAGE_ROOT), 'rev-parse', 'HEAD'], text=True).strip()
        result['dirty'] = bool(subprocess.check_output(['git', '-C', str(STORAGE_ROOT), 'status', '--porcelain'], text=True))
    return result

def record_build():
    (ROOT / 'build/storage-build.json').write_text(json.dumps(storage_version(), indent=2) + '\n')

def require_build():
    path = ROOT / 'build/storage-build.json'
    if not path.is_file():
        raise RuntimeError('Storage build record missing; run ./run.sh build')
    saved = json.loads(path.read_text())
    if saved['root'] != str(STORAGE_ROOT) or saved['version'] != expected['version']:
        raise RuntimeError('STORAGE_STACK_ROOT/version changed; rebuild this driver with ./run.sh build')

if __name__ == '__main__':
    import sys
    if sys.argv[1:] != ['--record-build']:
        raise SystemExit('usage: storage_dependency.py --record-build')
    record_build()

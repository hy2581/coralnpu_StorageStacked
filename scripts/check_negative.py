#!/usr/bin/env python3
"""Reject fabricated source evidence, wrong compute bytes and changed waveforms."""
from storage_dependency import STORAGE_ROOT, MEMSIM_BUILD, storage_version
import csv,json,shutil,sys,tempfile
from pathlib import Path
from verify import verify
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(STORAGE_ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'simulation/scripts'))
from audit_wave import audit
from check import check
source,output=map(Path,sys.argv[1:])
required=('resolved.json','completion.json','config.json','memsim_config.json','protocol_summary.json',
          'check_summary.json','aou_check_summary.json','memsim_check.json','memsim_core.json',
          'transactions.csv','npu_requests.csv','axi_events.csv','memsim_bridge.csv','run.log')
results={}
for fault in ('missing_source_response','source_write_byte','source_read_byte','source_id','early_source_response','axi_read_byte','wave_data_bit'):
    with tempfile.TemporaryDirectory(prefix='coralnpu-negative-') as td:
        target=Path(td)
        for name in required:shutil.copy2(source/name,target/name)
        shutil.copytree(source/'wave_audit',target/'wave_audit')
        if fault=='wave_data_bit':
            # Mutate a transferred WDATA value in the VCD, leaving CSV untouched.
            vcd=(source/'axi_wave.vcd').read_text();code=None
            for line in vcd.splitlines():
                p=line.split()
                if len(p)>4 and p[0]=='$var' and p[4]=='wdata':code=p[3];break
            assert code
            changed=False;lines=[]
            for line in vcd.splitlines():
                p=line.split()
                if not changed and len(p)==2 and p[1]==code and p[0].startswith('b') and set(p[0][1:])<={'0','1'} and int(p[0][1:],2):
                    line='b'+format(int(p[0][1:],2)^(1<<8),'b')+' '+code;changed=True
                lines.append(line)
            assert changed;(target/'axi_wave.vcd').write_text('\n'.join(lines)+'\n')
        else:
            path=target/('axi_events.csv' if fault=='axi_read_byte' else 'npu_requests.csv')
            with path.open() as f:rows=list(csv.DictReader(f))
            if fault=='missing_source_response':rows.pop(next(i for i,r in enumerate(rows) if r['event']=='response'))
            elif fault=='axi_read_byte':
                row=next(r for r in rows if r['channel']=='R');row['data']=str(int(row['data'])^1)
            else:
                row=next(r for r in rows if (r['event']=='request' if fault=='source_write_byte' else r['event']=='response') and (r['command']=='W' if fault=='source_write_byte' else r['command']=='R'))
                if fault in ('source_write_byte','source_read_byte'):
                    data=bytearray.fromhex(row['data']);data[0]^=1;row['data']=data.hex()
                elif fault=='source_id':row['native_id']=str(int(row['native_id'])^1)
                else:row['tick']='0'
            with path.open('w') as f:w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
        try:
            if fault=='wave_data_bit':audit(target,target/'mutated-wave')
            elif fault=='axi_read_byte':check(target)
            else:verify(target)
        except (AssertionError,KeyError,ValueError) as e:results[fault]={'rejected':True,'reason':str(e)}
        else:raise AssertionError('Corrupted evidence accepted: '+fault)
output.write_text(json.dumps({'passed':True,'checks':results},indent=2)+'\n')
print(output.read_text())

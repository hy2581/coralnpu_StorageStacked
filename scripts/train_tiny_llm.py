#!/usr/bin/env python3
"""Train the project's small character decoder. Optional NumPy-only tool.

The inference/verification path uses only Python's standard library. All training
text is authored in config/llm/corpus.txt; no external model or dataset is used.
"""
import argparse
import json
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]

def train(config, corpus):
    rng = np.random.default_rng(config['seed'])
    vocabulary = ''.join(sorted(set(corpus)))
    tokens = np.array([vocabulary.index(c) for c in corpus], dtype=np.int64)
    d, h, f, c = (config[k] for k in ('dim','heads','hidden_dim','context_length'))
    v, dh = len(vocabulary), d // h
    p = {}
    def parameter(name, shape, kind='random'):
        p[name] = (np.ones(shape) if kind=='one' else np.zeros(shape) if kind=='zero'
                   else rng.normal(0, 0.15, shape)).astype(np.float32)
    parameter('embedding',(v,d)); parameter('position',(c,d))
    for name in ('norm1','norm2','norm_final'):
        parameter(name+'_weight',(d,),'one'); parameter(name+'_bias',(d,),'zero')
    for name in ('q','k','v','o'): parameter(name,(d,d))
    parameter('ff1',(d,f)); parameter('ff1_bias',(f,),'zero')
    parameter('ff2',(f,d)); parameter('ff2_bias',(d,),'zero')
    parameter('lm_head',(d,v)); parameter('lm_bias',(v,),'zero')
    first, second = ({k: np.zeros_like(x) for k,x in p.items()} for _ in range(2))
    history = []
    B, T = config['batch_size'], config['sequence_length']
    mask = np.triu(np.ones((T,T),dtype=bool),1)
    for step in range(1, config['steps']+1):
        starts = rng.integers(0,len(tokens)-T,size=B)
        ids = tokens[starts[:,None]+np.arange(T)]
        targets = tokens[starts[:,None]+np.arange(1,T+1)]
        g = {k: np.zeros_like(x) for k,x in p.items()}
        def norm(x, name):
            centered=x-x.mean(-1,keepdims=True)
            inv=1/np.sqrt((centered*centered).mean(-1,keepdims=True)+1e-5)
            z=centered*inv
            return z*p[name+'_weight']+p[name+'_bias'],(z,inv,name)
        def norm_back(grad,cache):
            z,inv,name=cache
            g[name+'_weight']+=(grad*z).sum((0,1))
            g[name+'_bias']+=grad.sum((0,1))
            dz=grad*p[name+'_weight']
            return inv*(dz-dz.mean(-1,keepdims=True)-z*(dz*z).mean(-1,keepdims=True))
        def linear_back(x,grad,name):
            g[name]+=x.reshape(-1,x.shape[-1]).T@grad.reshape(-1,grad.shape[-1])
            return grad@p[name].T
        def split(x): return x.reshape(B,T,h,dh).transpose(0,2,1,3)
        def merge(x): return x.transpose(0,2,1,3).reshape(B,T,d)
        x=p['embedding'][ids]+p['position'][None,:T]
        n1,nc1=norm(x,'norm1')
        q,k,val=(split(n1@p[name]) for name in ('q','k','v'))
        score=q@k.transpose(0,1,3,2)/np.sqrt(dh)
        score=np.where(mask,-1e9,score)
        att=np.exp(score-score.max(-1,keepdims=True)); att/=att.sum(-1,keepdims=True)
        mixed=merge(att@val)
        residual=x+mixed@p['o']
        n2,nc2=norm(residual,'norm2')
        ffpre=n2@p['ff1']+p['ff1_bias']; ff=np.maximum(ffpre,0)
        hidden=residual+ff@p['ff2']+p['ff2_bias']
        nf,ncf=norm(hidden,'norm_final')
        logits=nf@p['lm_head']+p['lm_bias']
        prob=np.exp(logits-logits.max(-1,keepdims=True)); prob/=prob.sum(-1,keepdims=True)
        loss=float(-np.log(prob[np.arange(B)[:,None],np.arange(T),targets]).mean())
        grad=prob.copy();grad[np.arange(B)[:,None],np.arange(T),targets]-=1;grad/=B*T
        g['lm_bias']+=grad.sum((0,1));dnf=linear_back(nf,grad,'lm_head')
        dhidden=norm_back(dnf,ncf)
        g['ff2_bias']+=dhidden.sum((0,1));dff=linear_back(ff,dhidden,'ff2')
        dff*=ffpre>0;g['ff1_bias']+=dff.sum((0,1))
        dn2=linear_back(n2,dff,'ff1');dr=dhidden+norm_back(dn2,nc2)
        dmixed=split(linear_back(mixed,dr,'o'))
        datt=dmixed@val.transpose(0,1,3,2)
        dv=att.transpose(0,1,3,2)@dmixed
        ds=att*(datt-(datt*att).sum(-1,keepdims=True))/np.sqrt(dh)
        dq=ds@k;dk=ds.transpose(0,1,3,2)@q
        dn1=sum(linear_back(n1,merge(grad),name) for grad,name in ((dq,'q'),(dk,'k'),(dv,'v')))
        dx=dr+norm_back(dn1,nc1)
        np.add.at(g['embedding'],ids,dx);g['position'][:T]+=dx.sum(0)
        magnitude=np.sqrt(sum(float((x*x).sum()) for x in g.values()))
        lr=config['learning_rate']*(0.2+0.8*(1-step/config['steps']))
        for name in p:
            grad=g[name]/max(magnitude,1.0)
            first[name]=0.9*first[name]+0.1*grad
            second[name]=0.999*second[name]+0.001*grad*grad
            p[name]-=lr*(first[name]/(1-0.9**step))/(np.sqrt(second[name]/(1-0.999**step))+1e-8)
        if step==1 or step%100==0 or step==config['steps']:
            history.append({'step':step,'cross_entropy':loss})
            print(f'step={step} cross_entropy={loss:.6f}',flush=True)
    assert history[-1]['cross_entropy'] < history[0]['cross_entropy']*0.5
    return {'format':'coral-tiny-char-v1','version':'tiny-char-trained-v1',
            'architecture':{'dim':d,'heads':h,'hidden_dim':f,'layers':1,'context_length':c,
                            'vocab_size':v,'normalization':'layernorm','epsilon':1e-5,'activation':'relu'},
            'vocabulary':vocabulary,'training':{'config':config,'numpy_version':np.__version__,
                'corpus':'config/llm/corpus.txt','history':history,
                'scope':'Small authored corpus, training loss only; no general-language accuracy claim'},
            'tensors':{name:{'shape':list(x.shape),'data':x.ravel().tolist()} for name,x in p.items()}}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'config/llm/tiny_char_v1.json')
    args=parser.parse_args()
    model=train(json.loads((ROOT/'config/llm/training.json').read_text()),(ROOT/'config/llm/corpus.txt').read_text())
    args.output.write_text(json.dumps(model,indent=2)+'\n')
    print('Saved',args.output)

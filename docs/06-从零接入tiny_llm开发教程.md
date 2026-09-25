# 06-从零接入 tiny_llm 开发教程

这篇教程假设：**你拿到的项目已经能运行 CoralNPU RTL、AXI/UCIe 和在线 mem_sim，也有普通内存自检，但还没有 tiny_llm 模型、设备程序、配置、编译目标、校验器和快捷命令。** 我们从这个起点开始，逐步把一个小型语言模型接进去，直到 NPU 真正执行推理、写回生成结果，并通过计算与完整存储链路的验收。

本文按开发依赖组织步骤，是对当前实现的重建说明。当前交付目录已经包含这些代码；已有的文件或分支直接对照阅读，不要重复添加，也不用先删除现有 tiny_llm。普通用户直接运行的方法见 [03-当前 LLM 负载与执行位置](03-当前LLM负载与执行位置.md)；本篇解释的是“这些能力最初要怎样接上”。

关键新增程序的完整源码放在各步骤的折叠块中，方便从空文件开始填写。模型权重由训练程序导出，不要求手写一千个数字。所有命令默认在项目根目录执行，输出目录必须是新目录。末尾的性能数字来自已经归档的实际验收，本次新增教程没有重新执行 RTL 全套验收。

## 第 0 步：先确认没有 LLM 时，公共链路已经能运行

先阅读 [README.md](../README.md) 和 [config/README.md](../config/README.md)，进入项目：

```bash
cd /home/hy258/hy/zhongxing/coralnpu_StorageStacked
./run.sh setup
./run.sh build
./run.sh check
./run.sh run --benchmark config/benchmark.json --output results/tutorial-llm-baseline
```

在未改动的当前项目中，`config/benchmark.json` 选择 `memory_roundtrip`。这一步先让普通内存程序通过，再接 LLM；已经准备并验收过环境时，可以使用已有基线。

这里已有的链路是：

```text
CoralNPU RTL → 原生异步接口 → AXI256 → AXI2Flit → UCIe → 在线 mem_sim
```

`setup` 准备本工程依赖，`build` 构建设备库、普通内存程序和公共仿真器，`check` 检查环境。只有最后的实际运行及 `summary.json` 中的 `passed: true`，才说明基线计算和链路通过。

**完成本步后：**已有一个能装载 ELF、推进 RTL、执行外部读写、保存和检查证据的工程。新增 tiny_llm 主要扩展负载、配置、构建和计算验收，不需要为它另建一套内存仿真器。

## 第 1 步：先定义要做的模型和通过条件

目标是一个能在 RTL 仿真中跑完整流程的小型 decoder Transformer。这里的“完整”指从提示词到逐 token 生成的推理流程；模型规模和语言能力很小。

| 项目 | 本次约定 |
|---|---|
| 输入与分词 | 英文小语料；一个字符对应一个 token |
| 模型规模 | 1 层、隐藏状态 8 维、2 个注意力头、每头 4 维 |
| 前馈网络 | 8 → 16 → 8，ReLU |
| 归一化 | 注意力前、FFN 前和最终输出前各一次 LayerNorm，epsilon 为 `1e-5` |
| 位置表示 | 学习得到的位置嵌入 |
| 词表与上下文 | 当前词表 17 字符，最多处理 16 个位置 |
| 数值与执行 | NPU 标量 FP32，RVV 关闭，batch=1 |
| 生成规则 | 取 logits 最大值对应的 token，固定生成指定数量 |
| 默认用例 | 输入 `"red "`，生成 3 个 token，当前模型结果为 `"blu"` |

先约定验收条件：

1. 词嵌入、LayerNorm、Q/K/V、注意力、FFN、logits 和 token 选择都在 CoralNPU 中计算。
2. 推理权重、K/V、输入 token、输出记录通过现有外部内存链路读写。
3. Python 提供独立参考，检查每个位置的中间结果和最终 token；不能只检查输出字符串。
4. 开启 KV cache 时复用历史位置，关闭时重算整个前缀，两者生成结果相同。
5. 放慢内存后结果不变，实际返回延迟影响 NPU 执行；反压与重放场景同样能通过。
6. 错误权重、错误 logits、错误 token、错误 KV 和缺失记录必须被检查器拒绝。

没有把预期的 `"blu"` 写进设备程序。它只是当前训练权重对应的参考结果；替换权重或提示词后，应重新计算参考答案。

## 第 2 步：建立模型文件，先解决“权重从哪里来”

先建立目录：

```bash
mkdir -p benchmarks/tiny_llm config/llm config/benchmarks
```

原始模型和公开参数全部放在 `config/`，设备程序放在 `benchmarks/tiny_llm/`。不要把训练脚本、编译产物和模型文件混在一个 benchmark 目录中。

### 2.1 写训练语料和训练配置

新建 [config/llm/corpus.txt](../config/llm/corpus.txt)，内容为两行，每行末尾有换行：

<!-- TINY_LLM_CORPUS_BEGIN -->
```text
red blue. blue red. one two. two one. hi coral. coral hi.
red red blue. blue blue red. one one two. two two one.
```
<!-- TINY_LLM_CORPUS_END -->

换行也会进入字符词表。当前按字符排序得到的词表，用 Python 转义形式表示为 `"\n .abcdehilnortuw"`，共 17 个字符。

新建 [config/llm/training.json](../config/llm/training.json)：

<!-- TINY_LLM_TRAINING_CONFIG_BEGIN -->
```json
{
  "seed": 20260925,
  "steps": 1500,
  "batch_size": 32,
  "sequence_length": 12,
  "learning_rate": 0.015,
  "dim": 8,
  "heads": 2,
  "hidden_dim": 16,
  "context_length": 16
}
```
<!-- TINY_LLM_TRAINING_CONFIG_END -->

`sequence_length=12` 表示每次训练采样 12 个输入位置；`context_length=16` 是模型预留的位置数。两者含义不同，不要把“能容纳 16 个位置”理解为已经对所有长上下文质量做过评估。

### 2.2 新增可选训练程序

新建 [scripts/train_tiny_llm.py](../scripts/train_tiny_llm.py)。它使用 NumPy 完成前向、反向传播和 Adam 更新，任务是根据前面的字符预测下一个字符，最后把参数导出为普通 JSON。

<details>
<summary>完整训练程序：新建 scripts/train_tiny_llm.py 时使用</summary>

<!-- TINY_LLM_TRAINER_BEGIN -->
```python
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
```
<!-- TINY_LLM_TRAINER_END -->

</details>

对于**确实没有模型文件的新接入副本**，建立独立训练环境并导出模型：

```bash
.deps/toolchain/bin/python -m venv .deps/llm-training
.deps/llm-training/bin/python -m pip install numpy==2.2.6
.deps/llm-training/bin/python scripts/train_tiny_llm.py --output build/tutorial-tiny-char.json
```

先检查训练日志和导出的模型。确认格式、损失记录正常，且目标文件尚不存在时，再将它作为初始模型保存：

```bash
cp -n build/tutorial-tiny-char.json config/llm/tiny_char_v1.json
```

**当前交付项目已经有这个文件，可以跳过训练和复制。** 重训练得到的新模型先保留在 `build/`；若要部署另一个版本，使用新的版本名和 `config/llm/` 文件名，再通过 benchmark 选择。训练脚本未指定 `--output` 时会写默认模型，所以这里明确指定临时导出位置。

常规构建、推理与校验只用 Python 标准库，不依赖 NumPy。NumPy 只用于这一步的可选训练。重新训练所得结果须独立验收，不能直接沿用旧模型的生成文本或性能数字。

### 2.3 明确模型 JSON 的格式

导出的 [tiny_char_v1.json](../config/llm/tiny_char_v1.json) 由五部分组成：

| 字段 | 含义 |
|---|---|
| `format` | 当前为 `coral-tiny-char-v1`，表明模型布局约定 |
| `version` | 当前交付模型为 `tiny-char-trained-v1` |
| `architecture` / `vocabulary` | 结构、激活、归一化和字符到 ID 的映射 |
| `training` | 配置、NumPy 版本、训练损失记录和语料说明 |
| `tensors` | 每个张量的 `shape` 和按行展开的 `data` |

18 个张量的参数数量如下。矩阵使用“输入维度 × 输出维度”的顺序，与后面的 `linear()` 一致。

| 张量 | 形状或组成 | 参数数 |
|---|---|---:|
| `embedding` | 17 × 8 | 136 |
| `position` | 16 × 8 | 128 |
| 三组 LayerNorm 的 weight/bias | 每组 8 + 8 | 48 |
| `q`、`k`、`v`、`o` | 每个 8 × 8 | 256 |
| `ff1` / `ff1_bias` | 8 × 16 / 16 | 144 |
| `ff2` / `ff2_bias` | 16 × 8 / 8 | 136 |
| `lm_head` / `lm_bias` | 8 × 17 / 17 | 153 |
| 合计 | | **1001** |

当前交付模型记录的训练交叉熵从约 3.017709 降至 0.171689，共 1500 步。这是训练损失，没有独立测试集语言质量结论。

**完成本步后：**有了一个真实训练得到、格式明确的模型。模型文件存在或训练损失下降，都还不能说明 NPU 推理已经接通。

## 第 3 步：先写宿主参考和共享布局，再写 NPU 程序

新建 [scripts/llm_model.py](../scripts/llm_model.py)，把模型检查、内存布局、字符编码、参考计算和头文件生成放在同一个模块中。

| 函数 | 要解决的问题 |
|---|---|
| `load_model()` | 拒绝不支持的模型结构、错误形状、非有限或异常权重 |
| `layout()` | 给权重和中间记录分配偏移，检查区域容量 |
| `encode()` | 将非空提示词转成字符 ID；拒绝词表外字符 |
| `forward()` | 用宿主 Python 浮点运算计算完整前缀，得到各位置各阶段结果 |
| `reference()` | 按完整前缀重算，逐次选择下一个 token |
| `generate_header()` | 将模型、布局和负载参数写成 NPU 编译使用的 C++ 头文件 |

<details>
<summary>完整参考与布局模块：新建 scripts/llm_model.py 时使用</summary>

<!-- TINY_LLM_MODEL_HELPER_BEGIN -->
```python
"""Model validation, memory layout and an independent float64 full-prefix reference."""
import json
import math
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEIGHTS, KEY_CACHE, VALUE_CACHE = 0x90000000, 0x90010000, 0x90020000
TRACE, TOKENS, REPORT = 0x90030000, 0x90040000, 0x90050000

def load_model(path):
    p=Path(path)
    m=json.loads((p if p.is_absolute() else ROOT/p).read_text())
    a=m['architecture']
    assert m['format']=='coral-tiny-char-v1' and a['layers']==1
    assert a['dim']==8 and a['heads']==2 and a['hidden_dim']==16 and a['context_length']==16
    assert a['normalization']=='layernorm' and a['activation']=='relu' and a['epsilon']==1e-5
    d,f,v,c=a['dim'],a['hidden_dim'],a['vocab_size'],a['context_length']
    assert 2<=v<=32 and len(m['vocabulary'])==len(set(m['vocabulary']))==v
    shapes={'embedding':[v,d],'position':[c,d], 'q':[d,d],'k':[d,d],'v':[d,d],'o':[d,d],
            'ff1':[d,f],'ff1_bias':[f],'ff2':[f,d],'ff2_bias':[d],'lm_head':[d,v],'lm_bias':[v]}
    for n in ('norm1','norm2','norm_final'):
        shapes[n+'_weight']=[d];shapes[n+'_bias']=[d]
    assert set(m['tensors'])==set(shapes)
    for name,shape in shapes.items():
        t=m['tensors'][name]
        assert t['shape']==shape and len(t['data'])==math.prod(shape),name
        assert all(type(x) in (int,float) and math.isfinite(x) and abs(x)<100 for x in t['data']),name
    return m

def layout(m):
    flat,offsets=[],{}
    for name,t in m['tensors'].items():
        offsets[name]=len(flat);flat+=t['data']
        flat += [0.0]*(-len(flat)%4)
    a=m['architecture'];d=a['dim']
    counts={'embedding':d,'norm1':d,'q':d,'k':d,'v':d,
            'attention':a['heads']*a['context_length'],'attention_output':d,
            'residual':d,'norm2':d,'ff':a['hidden_dim'],'hidden':d,
            'norm_final':d,'logits':a['vocab_size']}
    traces={};cursor=0
    for name,count in counts.items():
        traces[name]={'offset':cursor,'count':count};cursor+=(count+3)//4*4
    assert len(flat)*4<0x10000 and cursor*a['context_length']*4<0x10000
    return flat,offsets,traces,cursor

def encode(m,text):
    if not isinstance(text,str) or not text: raise ValueError('prompt must be a nonempty string')
    try:return [m['vocabulary'].index(c) for c in text]
    except ValueError:raise ValueError('prompt contains characters absent from model vocabulary') from None

def forward(m,tokens):
    """Full causal matrix computation, without the device's incremental KV logic."""
    a=m['architecture'];d,h,f,c=a['dim'],a['heads'],a['hidden_dim'],a['context_length'];dh=d//h
    w={k:v['data'] for k,v in m['tensors'].items()}
    def norm(x,name):
        mean=sum(x)/d;var=sum((v-mean)**2 for v in x)/d
        return [(v-mean)/math.sqrt(var+a['epsilon'])*w[name+'_weight'][j]+w[name+'_bias'][j] for j,v in enumerate(x)]
    def linear(x,name,bias=None):
        width=m['tensors'][name]['shape'][1]
        return [sum(x[i]*w[name][i*width+j] for i in range(len(x)))+(w[bias][j] if bias else 0) for j in range(width)]
    records=[]
    for pos,token in enumerate(tokens):
        x=[w['embedding'][token*d+j]+w['position'][pos*d+j] for j in range(d)]
        n=norm(x,'norm1')
        records.append({'embedding':x,'norm1':n,**{name:linear(n,name) for name in ('q','k','v')}})
    for pos,r in enumerate(records):
        probs=[0.0]*(h*c);mixed=[]
        for head in range(h):
            scores=[sum(r['q'][head*dh+j]*records[t]['k'][head*dh+j] for j in range(dh))/math.sqrt(dh) for t in range(pos+1)]
            exps=[math.exp(x-max(scores)) for x in scores];ps=[x/sum(exps) for x in exps]
            probs[head*c:head*c+len(ps)]=ps
            mixed += [sum(ps[t]*records[t]['v'][head*dh+j] for t in range(pos+1)) for j in range(dh)]
        att=linear(mixed,'o');res=[x+y for x,y in zip(r['embedding'],att)]
        n2=norm(res,'norm2');ff=[max(0,x) for x in linear(n2,'ff1','ff1_bias')]
        hidden=[x+y for x,y in zip(res,linear(ff,'ff2','ff2_bias'))]
        nf=norm(hidden,'norm_final')
        r.update(attention=probs,attention_output=att,residual=res,norm2=n2,ff=ff,hidden=hidden,
                 norm_final=nf,logits=linear(nf,'lm_head','lm_bias'))
    return records

def reference(m,b):
    tokens=encode(m,b['prompt']);generated=[]
    for _ in range(b['generated_tokens']):
        logits=forward(m,tokens)[-1]['logits']
        token=max(range(len(logits)),key=logits.__getitem__)
        generated.append(token);tokens.append(token)
    processed=tokens[:-1]
    return {'tokens':processed,'generated':generated,'text':''.join(m['vocabulary'][i] for i in generated),
            'records':forward(m,processed)}

def generate_header(m,b,path):
    values,offsets,traces,stride=layout(m);a=m['architecture'];prompt=encode(m,b['prompt'])
    lines=['// Generated from config/llm model and benchmark; edit the source JSON.', '#pragma once']
    for name,value in {'DIM':a['dim'],'HEADS':a['heads'],'HIDDEN':a['hidden_dim'],'CONTEXT':a['context_length'],
                       'VOCAB':a['vocab_size'],'WEIGHT_WORDS':len(values),'TRACE_STRIDE':stride,
                       'PROMPT_LENGTH':len(prompt),'GENERATE':b['generated_tokens'],'KV_CACHE':int(b['kv_cache'])}.items():
        lines.append(f'#define LLM_{name} {value}')
    for name,offset in offsets.items():lines.append(f'#define W_{name.upper()} {offset}')
    for name,info in traces.items():lines.append(f'#define T_{name.upper()} {info["offset"]}')
    # Writable/volatile initial image stays in DTCM, away from the 8 KiB instruction store.
    lines.append('static volatile float model_image[LLM_WEIGHT_WORDS] = {')
    for i in range(0,len(values),8):lines.append(','.join(float(x).hex()+'f' for x in values[i:i+8])+',')
    lines.append('};')
    lines.append('static volatile unsigned prompt_image[LLM_PROMPT_LENGTH] = {'+','.join(map(str,prompt))+'};')
    path.write_text('\n'.join(lines)+'\n')
```
<!-- TINY_LLM_MODEL_HELPER_END -->

</details>

### 3.1 先规定外部内存地址

本次复用现有 `0x90000000` 起的外部窗口，不改公共地址 ABI：

| 起始地址 | 内容 | 当前容量或规则 |
|---|---|---|
| `0x90000000` | 推理权重 | 1004 个 FP32 槽，4016 字节，包含对齐填充 |
| `0x90010000` | K cache | 16 × 8 个 FP32，512 字节 |
| `0x90020000` | V cache | 同上 |
| `0x90030000` | 中间结果与 logits | 每位置 148 个 FP32 槽；16 个位置占 9472 字节 |
| `0x90040000` | 提示词与继续输入的 token | uint32 ID，最多 16 个已处理位置 |
| `0x90050000` | 输出 token、阶段标记、调用次数和错误数 | uint32 记录 |
| `0xc0000000` | 完成 mailbox | 设备控制状态，不作为外部 DRAM 数据统计 |

每个权重张量尾部补齐到 4 个 float 的倍数，所以 **1001 个模型参数对应 1004 个存储槽**。填充不增加模型参数。偏移单位是“float 槽”，转成字节地址时要乘 4。

`layout()` 按模型文件中的张量顺序生成 `W_*` 偏移，并检查权重、记录区不会越过各自的 64 KiB 区间。不要在设备程序里再手写一套张量偏移。

记录区的每个位置包含 13 个阶段：embedding、norm1、q、k、v、attention、attention_output、residual、norm2、ff、hidden、norm_final、logits。有效值共 145 个，补齐后步长为 148 个 float。

### 3.2 参考实现为什么按完整前缀计算

NPU 将采用增量 KV cache。宿主参考则每次把整个前缀重新算一遍，用另一种执行组织来核对相同的数学结果。这样更容易发现“历史 K/V 写错、位置偏移错、错误复用旧位置”等增量实现问题。

注意力只允许位置 `pos` 读取 `0..pos` 的 K/V；输出 logits 的最大值决定下一个字符。宿主参考不会把答案传回 NPU，它只在仿真之后参与验收。

此时还没有 benchmark 配置，可以先独立检查模型和参考计算：

```bash
.deps/toolchain/bin/python - <<'PY'
import sys
sys.path.insert(0, 'scripts')
from llm_model import load_model, layout, reference
m = load_model('config/llm/tiny_char_v1.json')
flat, offsets, fields, stride = layout(m)
b = {'prompt': 'red ', 'generated_tokens': 3, 'kv_cache': True}
r = reference(m, b)
print('parameters:', sum(len(t['data']) for t in m['tensors'].values()))
print('weight words:', len(flat), 'trace stride:', stride)
print('generated:', repr(r['text']), r['generated'])
PY
```

随项目提供的模型应得到 `1001`、`1004`、`148` 和 `"blu" [4, 10, 15]`。这一步只验证宿主模型格式与参考结果，还没有运行 NPU。

## 第 4 步：新建在 NPU 上执行的完整推理程序

新建 [benchmarks/tiny_llm/kernel.cc](../benchmarks/tiny_llm/kernel.cc)。它包含三类操作：初始化外部数据、执行前向与生成、写出验收记录。

<details>
<summary>完整 NPU 程序：新建 benchmarks/tiny_llm/kernel.cc 时使用</summary>

<!-- TINY_LLM_KERNEL_BEGIN -->
```cpp
// Small trained decoder: all arithmetic executes in the CoralNPU RTL core.
// External weights, tokens, KV cache and observations use the production memory path.
#include <cstdint>
#include <cmath>
#include "llm_config.h"

static volatile float* const weights = reinterpret_cast<volatile float*>(0x90000000u);
static volatile float* const keys = reinterpret_cast<volatile float*>(0x90010000u);
static volatile float* const values = reinterpret_cast<volatile float*>(0x90020000u);
static volatile float* const trace = reinterpret_cast<volatile float*>(0x90030000u);
static volatile uint32_t* const tokens = reinterpret_cast<volatile uint32_t*>(0x90040000u);
static volatile uint32_t* const report = reinterpret_cast<volatile uint32_t*>(0x90050000u);
static uint32_t errors = 0, calls = 0;

static void dump(unsigned pos, unsigned offset, const float* data, unsigned count) {
    for (unsigned i=0; i<count; ++i) trace[pos*LLM_TRACE_STRIDE+offset+i]=data[i];
}
static void norm(const float* x, float* y, unsigned scale, unsigned bias) {
    float mean=0.0f, variance=0.0f;
    for (unsigned i=0;i<LLM_DIM;++i) mean+=x[i];
    mean/=LLM_DIM;
    for (unsigned i=0;i<LLM_DIM;++i) {float z=x[i]-mean;variance+=z*z;}
    const float inv=1.0f/sqrtf(variance/LLM_DIM+1e-5f);
    for (unsigned i=0;i<LLM_DIM;++i) y[i]=(x[i]-mean)*inv*weights[scale+i]+weights[bias+i];
}
static void linear(const float* x, float* y, unsigned rows, unsigned cols, unsigned matrix, int bias=-1) {
    for (unsigned j=0;j<cols;++j) {
        float sum=0.0f;
        for (unsigned i=0;i<rows;++i) sum+=x[i]*weights[matrix+i*cols+j];
        y[j]=sum+(bias>=0?weights[bias+j]:0.0f);
    }
}
static unsigned forward(unsigned pos) {
    ++calls;
    float x[LLM_DIM], n1[LLM_DIM], q[LLM_DIM], k[LLM_DIM], v[LLM_DIM];
    float mixed[LLM_DIM], projected[LLM_DIM], residual[LLM_DIM], n2[LLM_DIM];
    float ff[LLM_HIDDEN], hidden[LLM_DIM], nf[LLM_DIM], logits[LLM_VOCAB];
    float probs[LLM_HEADS*LLM_CONTEXT]={};
    const unsigned id=tokens[pos];
    if(id>=LLM_VOCAB) {++errors;return 0;}
    for(unsigned j=0;j<LLM_DIM;++j) x[j]=weights[W_EMBEDDING+id*LLM_DIM+j]+weights[W_POSITION+pos*LLM_DIM+j];
    dump(pos,T_EMBEDDING,x,LLM_DIM);
    norm(x,n1,W_NORM1_WEIGHT,W_NORM1_BIAS); dump(pos,T_NORM1,n1,LLM_DIM);
    linear(n1,q,LLM_DIM,LLM_DIM,W_Q);linear(n1,k,LLM_DIM,LLM_DIM,W_K);linear(n1,v,LLM_DIM,LLM_DIM,W_V);
    dump(pos,T_Q,q,LLM_DIM);dump(pos,T_K,k,LLM_DIM);dump(pos,T_V,v,LLM_DIM);
    for(unsigned j=0;j<LLM_DIM;++j) {keys[pos*LLM_DIM+j]=k[j];values[pos*LLM_DIM+j]=v[j];}
    constexpr unsigned head_dim=LLM_DIM/LLM_HEADS;
    for(unsigned h=0;h<LLM_HEADS;++h) {
        float peak=-1e30f,total=0.0f;
        for(unsigned t=0;t<=pos;++t) {
            float score=0.0f;
            for(unsigned j=0;j<head_dim;++j) score+=q[h*head_dim+j]*keys[t*LLM_DIM+h*head_dim+j];
            score/=sqrtf(float(head_dim));probs[h*LLM_CONTEXT+t]=score;
            if(score>peak) peak=score;
        }
        for(unsigned t=0;t<=pos;++t) {float p=expf(probs[h*LLM_CONTEXT+t]-peak);probs[h*LLM_CONTEXT+t]=p;total+=p;}
        float check=0.0f;
        for(unsigned t=0;t<=pos;++t) {probs[h*LLM_CONTEXT+t]/=total;check+=probs[h*LLM_CONTEXT+t];}
        if(!(check>0.999f && check<1.001f)) ++errors;
        for(unsigned j=0;j<head_dim;++j) {
            float sum=0.0f;
            for(unsigned t=0;t<=pos;++t) sum+=probs[h*LLM_CONTEXT+t]*values[t*LLM_DIM+h*head_dim+j];
            mixed[h*head_dim+j]=sum;
        }
    }
    dump(pos,T_ATTENTION,probs,LLM_HEADS*LLM_CONTEXT);
    linear(mixed,projected,LLM_DIM,LLM_DIM,W_O);dump(pos,T_ATTENTION_OUTPUT,projected,LLM_DIM);
    for(unsigned j=0;j<LLM_DIM;++j) residual[j]=x[j]+projected[j];
    dump(pos,T_RESIDUAL,residual,LLM_DIM);
    norm(residual,n2,W_NORM2_WEIGHT,W_NORM2_BIAS);dump(pos,T_NORM2,n2,LLM_DIM);
    linear(n2,ff,LLM_DIM,LLM_HIDDEN,W_FF1,W_FF1_BIAS);
    for(unsigned j=0;j<LLM_HIDDEN;++j) if(ff[j]<0.0f) ff[j]=0.0f;
    dump(pos,T_FF,ff,LLM_HIDDEN);
    linear(ff,hidden,LLM_HIDDEN,LLM_DIM,W_FF2,W_FF2_BIAS);
    for(unsigned j=0;j<LLM_DIM;++j) hidden[j]+=residual[j];
    dump(pos,T_HIDDEN,hidden,LLM_DIM);
    norm(hidden,nf,W_NORM_FINAL_WEIGHT,W_NORM_FINAL_BIAS);dump(pos,T_NORM_FINAL,nf,LLM_DIM);
    linear(nf,logits,LLM_DIM,LLM_VOCAB,W_LM_HEAD,W_LM_BIAS);dump(pos,T_LOGITS,logits,LLM_VOCAB);
    unsigned best=0;
    for(unsigned j=0;j<LLM_VOCAB;++j) {
        if(!(logits[j]>-1e10f && logits[j]<1e10f)) ++errors;
        if(logits[j]>logits[best]) best=j;
    }
    return best;
}
int main() {
    report[0]=0x4c4c4d31u;
    for(unsigned i=0;i<LLM_WEIGHT_WORDS;++i) weights[i]=model_image[i];
    for(unsigned i=0;i<LLM_CONTEXT*LLM_DIM;++i) {keys[i]=0.0f;values[i]=0.0f;}
    for(unsigned i=0;i<LLM_PROMPT_LENGTH;++i) tokens[i]=prompt_image[i];
    report[1]=1;
    for(unsigned step=0;step<LLM_GENERATE;++step) {
        report[32+step]=0x70000000u+step;
        const unsigned end=LLM_PROMPT_LENGTH+step;
        unsigned best=0;
        const unsigned begin=LLM_KV_CACHE && step?end-1:0;
        for(unsigned pos=begin;pos<end;++pos) best=forward(pos);
        report[16+step]=best;
        const unsigned returned=report[16+step];
        if(returned!=best) ++errors;
        if(step+1<LLM_GENERATE) tokens[end]=returned;
        report[96+step]=calls;
        report[64+step]=0x71000000u+step;
    }
    report[8]=errors;report[2]=2;
    *reinterpret_cast<volatile uint32_t*>(0xc0000000u)=errors?0xbad00000u:0x600d0000u;
    asm volatile("wfi");
    return 0;
}
```
<!-- TINY_LLM_KERNEL_END -->

</details>

### 4.1 初始化由 NPU 自己完成

生成头文件中的 `model_image` 和 `prompt_image` 随 ELF 装入本地 TCM。`main()` 启动后依次：

1. 写 `report[0]`，表示初始化开始。
2. 将 1004 个权重槽通过外部链路写入 `weights`。
3. 清零全部 K/V 槽，并将提示词 ID 写入 `tokens`。
4. 写 `report[1]`，表示初始化完成。

之后 `linear()`、`norm()` 等通过外部 `weights` 指针读取参数。`volatile` 指针保证这些访问作为实际内存访问保留；当前位置的小型激活数组留在本地栈中。

这种安排让外部权重初始化也有可检查的写请求。ELF 装载器只初始化本地 TCM，不向 mem_sim 偷写一份外部数据。

### 4.2 一个位置的前向做什么

`forward(pos)` 的顺序为：

```text
读取 token ID
  → 词嵌入 + 位置嵌入
  → LayerNorm
  → Q/K/V 投影，将本位置 K/V 写入外部 cache
  → 对历史位置做因果注意力与 softmax
  → 注意力输出投影 + 残差
  → LayerNorm → FFN/ReLU + 残差
  → 最终 LayerNorm → logits → argmax
```

这里的 `sqrtf()`、`expf()` 和矩阵乘加由 NPU 程序执行。`dump()` 把各阶段结果另外写入外部记录区，供宿主逐层检查。输出端直接比较 logits 选 token，不必再做一次 softmax；注意力内部仍有 softmax。

### 4.3 用同一个循环实现 prefill、decode 和关闭缓存的对照

关键代码是：

```cpp
const unsigned end = LLM_PROMPT_LENGTH + step;
const unsigned begin = LLM_KV_CACHE && step ? end - 1 : 0;
for (unsigned pos = begin; pos < end; ++pos) best = forward(pos);
```

默认提示词有 4 个字符，生成 3 个 token：

| 生成步骤 | 输入前缀 | 开启缓存时计算的位置 | 关闭缓存时计算的位置 | 当前模型输出 |
|---|---|---|---|---|
| step 0，prefill | `red ` | 0、1、2、3 | 0、1、2、3 | `b` |
| step 1，decode | `red b` | 4 | 0、1、2、3、4 | `l` |
| step 2，decode | `red bl` | 5 | 0、1、2、3、4、5 | `u` |

开启缓存总共调用 `forward()` 6 次，关闭后为 15 次。最后的 `u` 已经是输出，不再送回模型，所以处理位置数是 `prompt_length + generated_tokens - 1`。

关闭缓存时，K/V 数组仍作为当前完整前缀计算的工作区使用，只是不复用上一轮已经计算过的前缀。不能把 `kv_cache=false` 理解为整个程序不再读写 K/V。

### 4.4 输出回读与设备完成

生成的 token 先写到 `report[16+step]`，再从外部内存读回；需要下一轮时，把读回值写到 `tokens[end]`。因此下一步输入依赖真实的外部返回数据。

| report 槽 | 内容 |
|---|---|
| `[0]` | `0x4c4c4d31`，初始化开始 |
| `[1]` / `[2]` | 初始化完成 / 全部推理完成 |
| `[8]` | 设备内部检查错误数，成功时为 0 |
| `[16+step]` | 生成的 token ID |
| `[32+step]` / `[64+step]` | 本轮开始 / 本轮结束标记 |
| `[96+step]` | 累计前向位置计算次数 |

最后写 mailbox `0x600d0000` 或错误标记，并执行 `wfi`。设备内检查包括 token 范围、注意力概率和、logits 有限范围、输出回读相等；完整数值正确性还要由后面的独立校验器判断。

**完成本步后：**已有设备程序，但它依赖的 `llm_config.h` 尚未生成，也没有接上编译和运行入口。

## 第 5 步：添加 benchmark 配置与参数校验

新建 [config/benchmarks/tiny_llm.json](../config/benchmarks/tiny_llm.json)：

<!-- TINY_LLM_BENCHMARK_BEGIN -->
```json
{
  "name": "tiny_llm",
  "model": "config/llm/tiny_char_v1.json",
  "prompt": "red ",
  "generated_tokens": 3,
  "kv_cache": true
}
```
<!-- TINY_LLM_BENCHMARK_END -->

这个配置决定“用哪个模型、输入什么、生成几个字符、是否复用 KV”。设备时钟、内存和 AXI 参数仍由 `config/architecture.json` 管理。

修改 [scripts/configure.py](../scripts/configure.py) 的 `load()`，在已有 benchmark 名称判断链中加入 tiny_llm 分支；与原有分支组成一个 `if/elif/else`，保留其他负载检查：

<!-- TINY_LLM_CONFIG_BRANCH_BEGIN -->
```python
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
```
<!-- TINY_LLM_CONFIG_BRANCH_END -->

这里限制模型必须位于本工程 `config/llm/`，并检查：

- `prompt` 非空，字符全部在词表内。
- `generated_tokens` 为 1～8 的整数。
- `kv_cache` 为布尔值。
- `prompt_length + generated_tokens - 1 <= 16`。
- 不能多写或漏写 benchmark 字段。

然后检查名字选择已经接通：

```bash
.deps/toolchain/bin/python scripts/configure.py --benchmark-name config/benchmarks/tiny_llm.json
```

预期输出 `tiny_llm`。若失败，应先修复配置，不必启动 RTL 查错。

## 第 6 步：将模型和配置生成 C++ 头文件

### 6.1 加入源码复制与头文件生成分支

在 `scripts/configure.py` 的 `main()` 中找到 `if o.generate_kernel:`，将 tiny_llm 加入设备源码复制列表，并在普通内存头文件生成代码之前加入 LLM 分支。

以下是**当前布局下的相关代码片段**，不是替换整个 `main()`；如果副本还没有 SMOKE，只保留已存在的负载条目即可：

```python
target = ROOT / 'third_party/coralnpu/native'
for name, staged in (('memory_roundtrip', 'ddr_touch'),
                     ('tiny_llm', 'tiny_llm'), ('smoke', 'smoke')):
    shutil.copy2(ROOT / 'benchmarks' / name / 'kernel.cc',
                 target / (staged + '.cc'))
# 保留已有 SMOKE 分支。
if b['name'] == 'tiny_llm':
    from llm_model import load_model, generate_header
    generate_header(load_model(b['model']), b, target / 'llm_config.h')
    return
# 后面保留普通内存负载的头文件生成代码。
```

第 3 步的 `generate_header()` 会生成：模型维度和配置宏、权重偏移 `W_*`、中间记录偏移 `T_*`、启动权重数组 `model_image`、提示词 ID 数组 `prompt_image`。

头文件中的权重用十六进制浮点常量表示，编译为 FP32。启动镜像使用可写的 `static volatile` 数组，放在 DTCM，避免把几千字节的权重放进容量较小的指令存储区。

### 6.2 构建记录必须包含实际模型内容

在 `build_inputs(a, b)` 中记录模型及张量顺序。当前完整函数为：

<!-- TINY_LLM_BUILD_INPUTS_BEGIN -->
```python
def build_inputs(a, b):
    result = {'device': DEVICE, 'runtime': 'native-v3-benchmarks', 'benchmark': b}
    if b['name'] == 'tiny_llm':
        from llm_model import load_model
        result['model'] = load_model(b['model'])
        result['model_tensor_order'] = list(result['model']['tensors'])
    return result
```
<!-- TINY_LLM_BUILD_INPUTS_END -->

不能只记录 `model` 路径：同一个文件的权重可能被修改。`model_tensor_order` 也要单独保存，因为字典内容相同不代表物理排列顺序相同，而 `layout()` 使用该顺序。

`runtime` 是本项目当前的构建输入版本标记，已经包含 SMOKE 等负载整理；对照当前实现保留它即可。它不是硬件 ISA 版本。

### 6.3 检查实际生成内容

第 0 步构建已经建立 `third_party/coralnpu/native/`。执行：

```bash
.deps/toolchain/bin/python scripts/configure.py --generate-kernel config/benchmarks/tiny_llm.json
rg '^#define LLM_' third_party/coralnpu/native/llm_config.h
```

当前默认配置应包含：

```cpp
#define LLM_DIM 8
#define LLM_HEADS 2
#define LLM_HIDDEN 16
#define LLM_CONTEXT 16
#define LLM_VOCAB 17
#define LLM_WEIGHT_WORDS 1004
#define LLM_TRACE_STRIDE 148
#define LLM_PROMPT_LENGTH 4
#define LLM_GENERATE 3
#define LLM_KV_CACHE 1
```

三个文件的关系是：原始程序 `benchmarks/tiny_llm/kernel.cc` → 暂存程序 `third_party/coralnpu/native/tiny_llm.cc`，模型/配置 → 暂存头文件 `llm_config.h`。以后修改原始文件，重新生成，不直接编辑暂存文件。

## 第 7 步：新增 Bazel 目标并接上构建脚本

修改 [integration/coralnpu/BUILD.bazel](../integration/coralnpu/BUILD.bazel)，在已有 `coralnpu_v2_binary` 目标旁新增：

<!-- TINY_LLM_BAZEL_BEGIN -->
```python
coralnpu_v2_binary(
    name="tiny_llm", srcs=["tiny_llm.cc", "llm_config.h"], stack_size_bytes=4096,
    copts=["-Os", "-fno-tree-vectorize", "-fno-tree-slp-vectorize", "-ffp-contract=off", "-march=rv32imf_zicsr_zifencei"],
)
```
<!-- TINY_LLM_BAZEL_END -->

这些选项对应当前标量 FP32 实现：

| 选项 | 作用 |
|---|---|
| `stack_size_bytes=4096` | 给当前位置的临时数组和函数调用保留栈空间 |
| `-Os` | 优先减小代码体积，适应当前指令存储容量 |
| 两个 `-fno-...vectorize` | 禁止自动向量化 |
| `-ffp-contract=off` | 避免浮点乘加收缩改变这套数值比较的运算形式 |
| `-march=rv32imf_zicsr_zifencei` | 使用当前程序需要的 RV32 整数、乘除、单精度浮点等指令 |

`integration/coralnpu/install.sh` 会将这份 BUILD 安装到 `third_party/coralnpu/native/BUILD`；不要只改生成目录下的 BUILD。

检查 [scripts/build_device.sh](../scripts/build_device.sh) 已按 benchmark 名称选择目标。关键部分为：

```bash
benchmark_name=$("$AXI_PYTHON" "$SS_ROOT/scripts/configure.py" --benchmark-name "${1:-config/benchmark.json}")
kernel_target=$benchmark_name
[[ $benchmark_name != memory_roundtrip ]] || kernel_target=ddr_touch
targets=("//native:$kernel_target.elf")
```

因此 tiny_llm 选择 `//native:tiny_llm.elf`，最终复制到 `build/coralnpu/tiny_llm.elf`。保留脚本中 Bazel 环境参数、`cquery` 定位实际 ELF、复制产物和 `--record-build` 等逻辑。如果旧副本固定构建 `ddr_touch`，需把目标选择和产物复制一起改成按名称选择。

同时确保 `scripts/configure.py` 的 ELF 路径函数为：

```python
def kernel_path(b):
    return ROOT / 'build/coralnpu' / (b['name'] + '.elf')
```

公共 RTL 库已构建时，可以单独编译新程序：

```bash
bash scripts/build_device.sh config/benchmarks/tiny_llm.json --kernel-only
```

**完成本步后：**获得能交给 CoralNPU 装载的 RISC-V ELF。编译成功只说明程序能生成，不证明数值和外部链路已经通过。

## 第 8 步：让运行器保存模型快照并启动这个 ELF

修改 [scripts/run.py](../scripts/run.py)。它原有的自动构建、独占构建锁、新建结果目录和失败记录继续使用。

运行前先用 `build_inputs(a, b)` 与 `build/device-config.json` 比较。模型或 benchmark 改变时，调用：

```python
run(['bash', ROOT / 'scripts/build_device.sh', o.benchmark,
     '--kernel-only'], 'benchmark-build.log')
```

然后构造运行配置 `c`，由 `kernel_path(b)` 选 ELF。加入以下分支，把**当次模型内容**写入 `resolved.json`：

```python
if b['name'] == 'tiny_llm':
    from llm_model import load_model
    c['llm_model'] = load_model(b['model'])
```

这段应位于 `c` 建立之后、`dump(output / 'resolved.json', c)` 之前。以后即使默认模型文件被更新，旧运行仍可以用保存的模型快照解释和核对。

实际运行继续使用公共入口：

```python
run([simulator, output / 'resolved.json', output], 'run.log')
run([sys.executable, ROOT / 'scripts/validate.py', output], 'validation-run.log')
```

`simulator` 是 `build/native/coralnpu_sim`。它读取配置并推进 RTL；不是在 Python 中计算一份输出后把结果填进仿真。`environment.json` 记录源码版本、实际产物文件名和大小，`resolved.json` 保存架构、负载、模型快照和 ELF 路径。

配置或模型修改会触发 ELF 重建；**直接修改 C++ 或 RTL 源码后，应先执行 `./run.sh build`**，不要把配置比较当成任意源码变动检测器。

## 第 9 步：增加 LLM 校验器，并接入公共验收

### 9.1 新建专用校验器

新建 [scripts/verify_llm.py](../scripts/verify_llm.py)。输入为当次 `resolved.json` 中的模型/配置和 `npu_requests.csv` 中的真实请求、响应。

<details>
<summary>完整数值校验器：新建 scripts/verify_llm.py 时使用</summary>

<!-- TINY_LLM_VERIFIER_BEGIN -->
```python
"""Verify observed NPU writes against an independent full-prefix decoder reference."""
import collections
import csv
import json
import math
import struct
from llm_model import WEIGHTS, KEY_CACHE, VALUE_CACHE, TRACE, TOKENS, REPORT, layout, encode, reference

ABS_TOL, REL_TOL = 0.00002, 0.00002

def check_observations(c, source):
    b,m=c['benchmark'],c['llm_model']
    ref=reference(m,b)
    flat,offsets,traces,stride=layout(m)
    d,ctx=m['architecture']['dim'],m['architecture']['context_length']
    prompt=encode(m,b['prompt']);g=b['generated_tokens'];p=len(prompt)
    visits=collections.Counter()
    expected_calls=[];calls=0
    for step in range(g):
        positions=range(p+step-1,p+step) if b['kv_cache'] and step else range(p+step)
        for pos in positions:visits[pos]+=1;calls+=1
        expected_calls.append(calls)
    expected={};kind={}
    def add(address,values,label):
        assert address not in expected
        expected[address]=list(values);kind[address]=label
    for i,value in enumerate(flat):add(WEIGHTS+4*i,[value],'weight')
    for base,field in ((KEY_CACHE,'k'),(VALUE_CACHE,'v')):
        for pos in range(ctx):
            for j in range(d):
                add(base+4*(pos*d+j),[0.0]+([ref['records'][pos][field][j]]*visits[pos] if pos in visits else []),'kv_'+field)
    for pos,r in enumerate(ref['records']):
        for name,info in traces.items():
            for j,value in enumerate(r[name]):add(TRACE+4*(pos*stride+info['offset']+j),[value]*visits[pos],name)
    for i,token in enumerate(ref['tokens']):add(TOKENS+4*i,[token],'token_input')
    reports={0:0x4c4c4d31,1:1,2:2,8:0}
    for step,token in enumerate(ref['generated']):
        reports.update({16+step:token,32+step:0x70000000+step,64+step:0x71000000+step,96+step:expected_calls[step]})
    for i,value in reports.items():add(REPORT+4*i,[value],'report')
    response={r['sequence']:r for r in source if r['event']=='response'}
    observed=collections.Counter();max_error=collections.defaultdict(float);marker_ticks={};traffic=collections.defaultdict(collections.Counter)
    tensor_reads=collections.Counter();raw_writes=[]
    for r in source:
        if r['event']!='request':continue
        address=int(r['address']);is_write=r['command']=='W'
        region=('weights' if WEIGHTS<=address<KEY_CACHE else 'keys' if KEY_CACHE<=address<VALUE_CACHE
                else 'values' if VALUE_CACHE<=address<TRACE else 'intermediates' if TRACE<=address<TOKENS
                else 'tokens' if TOKENS<=address<REPORT else 'report' if REPORT<=address<REPORT+4096 else None)
        assert region is not None, 'LLM request outside declared regions'
        traffic[region]['writes' if is_write else 'reads']+=1
        if not is_write:
            if region=='weights':
                for name,off in offsets.items():
                    if WEIGHTS+4*off<=address<WEIGHTS+4*(off+len(m['tensors'][name]['data'])):tensor_reads[name]+=1
            continue
        mask=int(r['mask']);enabled=[i for i in range(16) if mask&(1<<i)]
        assert len(enabled)==4 and enabled==list(range(enabled[0],enabled[0]+4)) and enabled[0]%4==0
        word=address+enabled[0];raw=bytes.fromhex(r['data'])[enabled[0]:enabled[0]+4]
        assert word in expected, 'Unexpected LLM write at %#x'%word
        index=observed[word];assert index<len(expected[word]), 'Extra LLM write at %#x'%word
        target=expected[word][index];label=kind[word]
        if label in ('report','token_input'):
            got=int.from_bytes(raw,'little');assert got==target, f'{label} mismatch at {word:#x}: {got} != {target}'
        elif label=='weight':
            assert raw==struct.pack('<f',target), f'Weight staging mismatch at {word:#x}'
        else:
            got=struct.unpack('<f',raw)[0]
            assert math.isfinite(got) and abs(got-target)<=ABS_TOL+REL_TOL*abs(target), f'{label} mismatch at {word:#x}: {got} != {target}'
            max_error[label]=max(max_error[label],abs(got-target))
        observed[word]+=1
        rsp=response[r['sequence']]
        assert rsp['command']=='W' and rsp['data']==r['data'] and int(rsp['tick'])>int(r['tick'])
        if label=='report':marker_ticks[(word-REPORT)//4]=int(rsp['tick'])
        raw_writes.append((word,raw))
    assert all(observed[a]==len(v) for a,v in expected.items()), 'Missing weight/KV/intermediate/token/report writes'
    assert set(tensor_reads)==set(m['tensors']), 'An inference weight tensor was not read through external memory'
    assert all(traffic[r]['reads']>0 for r in ('weights','keys','values','tokens','report'))
    starts=[marker_ticks[32+i] for i in range(g)];ends=[marker_ticks[64+i] for i in range(g)]
    ready=[marker_ticks[16+i] for i in range(g)]
    assert marker_ticks[0]<marker_ticks[1]<starts[0]
    for i in range(g):
        assert starts[i]<ready[i]<ends[i]
        if i:assert ends[i-1]<starts[i]
    assert ends[-1]<marker_ticks[2]
    return {'passed':True,'model_version':m['version'],'model_parameters':sum(len(t['data']) for t in m['tensors'].values()),
            'architecture':m['architecture'],'prompt':b['prompt'],'prompt_tokens':prompt,
            'generated_token_ids':ref['generated'],'generated_text':ref['text'],'kv_cache':b['kv_cache'],
            'processed_token_positions':p+g-1,'forward_calls':calls,'verified_float_writes':sum(observed[a] for a in expected if kind[a] not in ('weight','report','token_input')),
            'numeric_tolerance':{'absolute':ABS_TOL,'relative':REL_TOL},'maximum_absolute_error_by_stage':dict(max_error),
            'traffic':{k:dict(v) for k,v in traffic.items()},'weight_tensor_reads':dict(tensor_reads),
            'timing':{'unit':'ns','initialization_ns':(marker_ticks[1]-marker_ticks[0])/1e6,
                      'prefill_to_first_token_ns':(ready[0]-starts[0])/1e6,
                      'decode_inter_token_ns':[(ready[i]-ready[i-1])/1e6 for i in range(1,g)],
                      'decode_tokens_per_simulated_second':(g-1)*1e15/(ready[-1]-ready[0]) if g>1 else None,
                      'inference_to_last_token_ns':(ready[-1]-starts[0])/1e6,
                      'phase_duration_ns':[(ends[i]-starts[i])/1e6 for i in range(g)],
                      'scope':'RTL simulation including observation/marker traffic; excludes model staging and startup from prefill/decode'},
            'scope':'Trained miniature character decoder functional benchmark; scalar FP32, one layer, no general-language quality claim'}

def verify_llm(root,c):
    with (root/'npu_requests.csv').open() as stream:source=list(csv.DictReader(stream))
    result=check_observations(c,source)
    (root/'llm_summary.json').write_text(json.dumps(result,indent=2)+'\n')
    t=result['timing']
    report=['# CoralNPU 微型语言模型 benchmark', '',
            '数值与访存检查通过。完整的 AXI/Flit/内存验收结果以本目录 summary.json 为准。', '',
            f"- 模型：{result['model_version']}，{result['model_parameters']} 个参数，标量 FP32。",
            f"- 输入：{json.dumps(result['prompt'], ensure_ascii=False)}",
            f"- 生成：{json.dumps(result['generated_text'], ensure_ascii=False)}，token ID：{result['generated_token_ids']}",
            f"- KV cache：{result['kv_cache']}；实际前向位置计算次数：{result['forward_calls']}。", '',
            '| 阶段 | 仿真时间 |', '|---|---:|',
            f"| 模型与输入初始化 | {t['initialization_ns']:.3f} ns |",
            f"| Prefill 至首 token | {t['prefill_to_first_token_ns']:.3f} ns |",
            f"| 推理至最后一个 token | {t['inference_to_last_token_ns']:.3f} ns |", '',
            f"后续 token 间隔（ns）：{t['decode_inter_token_ns']}。",
            f"解码吞吐：{t['decode_tokens_per_simulated_second']} token/仿真秒；只生成一个 token 时此值为空。", '',
            '时间由 NPU 发出的完成标记与 token 写回响应测量，包含中间结果记录开销；prefill/decode 不包含模型初始化。',
            '这是小语料训练的字符模型功能基准，不能用来代表通用大模型质量或真实芯片吞吐。', '',
            '数值详情见 llm_summary.json；逐请求链路页面见 memsim_view.html。', '']
    (root/'llm_report.md').write_text('\n'.join(report))
    return result
```
<!-- TINY_LLM_VERIFIER_END -->

</details>

校验器先独立计算参考，然后为每个外部写地址建立“应该写什么、应该写几次”的清单：

- 权重初始化与模型转成 FP32 后的字节必须完全一致，包括对齐填充。
- 每个位置的 K/V 和 13 类中间结果都要出现，值和重复次数要正确。
- 开启缓存默认计算 6 个位置；关闭后计算 15 次，重复记录也必须按次数检查。
- token ID、阶段标记、调用次数和错误数要求精确相等。
- 每个权重张量都必须发生外部读取，K/V、tokens、report 也必须有读取证据。
- 阶段标记和 token 写回必须有真实写完成响应，且时间顺序正确。

浮点计算采用：

```text
abs(NPU_FP32值 - 宿主参考值) <= 2e-5 + 2e-5 * abs(宿主参考值)
```

参考使用宿主双精度 Python 浮点运算，设备使用标量 FP32，所以允许小的舍入差异；token ID 不允许近似。检查器还拒绝非有限数值。运行校验脚本时使用正常 Python 模式，不要用 `python -O` 或设置 `PYTHONOPTIMIZE`，否则会关闭代码里的断言。

### 9.2 接进 scripts/verify.py

在 [scripts/verify.py](../scripts/verify.py) 完成公共事务/波形检查、读取设备周期后，加入 LLM 分支：

```python
if b['name'] == 'tiny_llm':
    from verify_llm import verify_llm
    llm = verify_llm(root, c)
    devices.update(prompt_tokens=len(llm['prompt_tokens']),
                   generated_tokens=len(llm['generated_token_ids']),
                   mailbox=completion['mailbox'])
```

将已有 SMOKE、普通内存计算校验接在同一条 `elif/else` 链中。普通内存的 `words`、`iterations` 和固定事务数规则只适用于 `memory_roundtrip`，不能拿来校验 LLM。

构造公共 `result` 后、写 `summary.json` 前再加入：

```python
if b['name'] == 'tiny_llm':
    result['llm'] = llm
```

公共验收仍由 [scripts/validate.py](../scripts/validate.py) 顺序执行：AXI 检查、AXI2Flit/AoU、链路、在线内存数据、VCD 波形，再到包含 LLM 的 `verify.py`。不要用 `llm_summary.json` 的通过替代总验收。

这里的证据相互衔接：专用校验器检查 NPU 写出的计算结果；公共检查继续证明这些字节实际经过五通道、Flit 和 mem_sim，并与读返回、最终内存镜像相符。

**完成本步后：**运行器能够把“设备完成”和“计算、数据、链路都正确”区分开，已经具备首次正式运行条件。

## 第 10 步：先用通用 run 命令跑通

先重新构建修改过的源码，再执行通用入口：

```bash
./run.sh build
./run.sh run --benchmark config/benchmarks/tiny_llm.json --output results/tutorial-tiny-llm-first
```

`build` 默认构建普通内存 ELF 和公共库；随后 `run` 发现负载变化，会自动生成并构建 tiny_llm ELF。前面做过单独编译也没关系，运行器会核对当次构建记录。

观察命令末尾的 `PASS`，并实际读取结果：

```bash
cat results/tutorial-tiny-llm-first/llm_report.md
.deps/toolchain/bin/python - <<'PY'
import json
from pathlib import Path
p = Path('results/tutorial-tiny-llm-first')
s = json.loads((p / 'summary.json').read_text())
assert s['passed'] and s['llm']['passed']
print('generated:', repr(s['llm']['generated_text']))
print('token IDs:', s['llm']['generated_token_ids'])
print('forward calls:', s['llm']['forward_calls'])
print('NPU cycles:', s['devices']['cycles'])
print('external transactions:', s['sources']['coralnpu']['transactions'])
PY
```

当前交付模型的基准结果为生成 `"blu"`、ID `[4, 10, 15]`、`forward_calls=6`。若你使用自己重新训练的模型，生成内容由该模型的参考计算决定。

第一次失败时，先看 `summary.json` 的 `stage`：`build benchmark` 检查编译日志，`simulate` 检查运行日志，`verify data and link` / `validate` 检查验收日志。不要因设备已经写完成 mailbox 就忽略后续失败。

## 第 11 步：沿着一次权重读取看懂中间链路

以 `linear()` 中的一次 `weights[...]` 读取为例：

1. `scripts/run.py` 选择 ELF 和当次配置，启动 `build/native/coralnpu_sim`。
2. [simulation/main.cc](../simulation/main.cc) 建立独立 SystemC 仿真，全局分辨率为 1 fs。
3. [coralnpu_native.cc](../integration/coralnpu/coralnpu_native.cc) 将 ELF 装入本地 TCM，通过包装器的 AXI 写操作设置入口和启动控制。
4. CoralNPU RTL 执行 load 指令，从 AXI master 发出外部访问。
5. 原生异步回调把请求交给 [simulation/axi_master.cc](../simulation/axi_master.cc)，保留地址、数据、字节掩码和请求 ID；没有容量时向上游反压。
6. 适配器驱动实际 AXI256 五通道，AXI2Flit 把事务封装成 Flit，经双向 UCIe 到内存侧。
7. [公共内存桥](https://github.com/hy2581/axi_StorageStacked/blob/main/storage_axi/memsim_backend.cc) 检查地址、转换模型内部偏移、提交在线请求，等待 mem_sim 的实际响应。
8. 返回沿 UCIe、AXI 路径回到原生接口，`coralnpu_complete()` 将读数据交回 RTL，依赖这笔数据的运算继续执行。

外部权重写入与 token 写回走相同的存储链路，只是使用写通道。ELF 装载、控制启动和最终 mailbox 要与推理的外部数据事务分开理解。

这里存在有界事务适配层，不能把它描述成两端 RTL 管脚完全逐线直连。当前原生 NPU beat 为 16 字节，后级 AXI 总线为 32 字节宽；单次 float 写入通常只有 4 个有效字节，靠字节掩码和 lane 位置保留含义。

| 证据文件 | 用它确认什么 |
|---|---|
| `npu_requests.csv` | NPU 原生请求与响应，按 `sequence` 配对 |
| `transactions.csv` | 请求从接收、AXI 完成到回调结束的生命周期 |
| `axi_events.csv` / `axi_wave.vcd` | 实际 AW/W/B/AR/R 握手、数据、ID、反压 |
| `ucie_flits.csv` | 实际 Flit 发送、返回与链路行为 |
| `memsim_bridge.csv` | AXI burst 与内存子请求的提交、完成和返回关系 |
| `memsim_commands.csv` / `memsim_dfi.csv` | 存储命令和行为级 DFI 记录 |
| `memsim_image.csv` | 最终存储字节，配合请求/响应检查 |
| `llm_summary.json` | 逐层误差、生成 token、各区流量与阶段时间 |

如果只想查看已经完成的原始证据，可运行 `./run.sh validate results/某次完整结果目录`。它会重做校验并重写该目录的验收摘要，不会重新执行 RTL；仅有归档摘要、缺少原始波形/CSV 的目录不能代替完整结果目录。

## 第 12 步：通用入口通过后，再增加 llm 快捷命令

修改 [run.sh](../run.sh)，在 `case` 中加入：

```bash
llm)
    source "$root/scripts/activate.sh"
    exec "$AXI_PYTHON" "$root/scripts/run.py" --benchmark config/benchmarks/tiny_llm.json "$@" ;;
```

同时在用法说明中加入 `llm`。之后可执行：

```bash
./run.sh llm --output results/tutorial-tiny-llm-shortcut
```

这只是同一个 `run.py` 的默认参数入口，不应另写一个只打印生成文本、绕过公共验收的运行脚本。

## 第 13 步：添加关闭缓存和链路对照实验

新建 [config/benchmarks/tiny_llm_no_cache.json](../config/benchmarks/tiny_llm_no_cache.json)：

<!-- TINY_LLM_NO_CACHE_BEGIN -->
```json
{
  "name": "tiny_llm",
  "model": "config/llm/tiny_char_v1.json",
  "prompt": "red ",
  "generated_tokens": 3,
  "kv_cache": false
}
```
<!-- TINY_LLM_NO_CACHE_END -->

再新建 [config/benchmarks/tiny_llm_long.json](../config/benchmarks/tiny_llm_long.json)：

<!-- TINY_LLM_LONG_BEGIN -->
```json
{
  "name": "tiny_llm",
  "model": "config/llm/tiny_char_v1.json",
  "prompt": "one two",
  "generated_tokens": 3,
  "kv_cache": true
}
```
<!-- TINY_LLM_LONG_END -->

执行下面的对照，每个结果目录都要新建：

```bash
./run.sh llm --benchmark config/benchmarks/tiny_llm_no_cache.json --output results/tutorial-tiny-llm-no-cache
./run.sh llm --benchmark config/benchmarks/tiny_llm_long.json --output results/tutorial-tiny-llm-long
./run.sh llm --scale 4 --output results/tutorial-tiny-llm-slow
./run.sh llm --replay --output results/tutorial-tiny-llm-replay
./run.sh llm --architecture config/architectures/backpressure.json --output results/tutorial-tiny-llm-backpressure
```

| 对照 | 必须确认的行为 |
|---|---|
| 关闭缓存 | 同模型、同提示词生成 token 相同；前向次数、权重读取和周期增加 |
| 长提示词 | 按新提示词重新计算参考；更多位置的 KV 和中间结果都被检查 |
| 慢内存 | 结果和事务数不变；实际内存周期变为 4 倍，NPU 周期和推理仿真时间增加 |
| CRC 重放 | 确实出现受检查的重放行为，返回数据与生成 token 保持正确 |
| 反压 | 小队列、单在途请求、响应等待和不同时钟下保持正确 |

反压配置来自 [config/architectures/backpressure.json](../config/architectures/backpressure.json)。若旧副本尚无此文件，新增以下完整配置：

<!-- TINY_LLM_BACKPRESSURE_BEGIN -->
```json
{
  "device_clock_mhz": 250,
  "axi": {
    "period_ns": 3,
    "outstanding": 1,
    "planes": 2,
    "stalls": true,
    "replay": false
  },
  "memory": {
    "channels": 2,
    "scale": 1,
    "queue": 1,
    "slots": 1,
    "response_hold": 7,
    "standard": "hbm4"
  },
  "max_ticks": 20000000000000
}
```
<!-- TINY_LLM_BACKPRESSURE_END -->

它把设备频率从默认 500 MHz 改为 250 MHz，所以不能只比较两个用例的“设备周期数”判断快慢，应比较统一仿真时间。`memory.scale` 改变内存周期，不会把整个模拟世界的时间直接乘四。

## 第 14 步：证明校验器真的能拒绝错误

新建 [scripts/check_llm_negative.py](../scripts/check_llm_negative.py)：

<details>
<summary>完整错误拒绝检查：新建 scripts/check_llm_negative.py 时使用</summary>

<!-- TINY_LLM_NEGATIVE_BEGIN -->
```python
#!/usr/bin/env python3
"""Reject wrong model data/compute/token/KV observations, even with paired replies."""
import copy
import csv
import json
import struct
import sys
import tempfile
from pathlib import Path
from llm_model import WEIGHTS, KEY_CACHE, TRACE, REPORT, layout, forward, encode
from verify_llm import check_observations

root,output=map(Path,sys.argv[1:])
c=json.loads((root/'resolved.json').read_text())
with (root/'npu_requests.csv').open() as f:original=list(csv.DictReader(f))
_,_,fields,_=layout(c['llm_model'])
logit=TRACE+4*fields['logits']['offset']
results={}
for fault in ('wrong_weight','wrong_logits','wrong_generated_token','wrong_kv','missing_intermediate'):
    rows=copy.deepcopy(original)
    def word(r):
        mask=int(r['mask']);lane=next(i for i in range(16) if mask&(1<<i))
        return int(r['address'])+lane,lane
    address={'wrong_weight':WEIGHTS,'wrong_logits':logit,'wrong_generated_token':REPORT+64,
             'wrong_kv':KEY_CACHE,'missing_intermediate':TRACE}[fault]
    candidates=[r for r in rows if r['event']=='request' and r['command']=='W' and word(r)[0]==address]
    r=candidates[-1] if fault=='wrong_kv' else candidates[0]
    if fault=='missing_intermediate':rows=[v for v in rows if v['sequence']!=r['sequence']]
    else:
        lane=word(r)[1];data=bytearray.fromhex(r['data'])
        if fault=='wrong_generated_token':value=(int.from_bytes(data[lane:lane+4],'little')+1)%c['llm_model']['architecture']['vocab_size'];encoded=value.to_bytes(4,'little')
        else:encoded=struct.pack('<f',struct.unpack('<f',data[lane:lane+4])[0]+1.0)
        data[lane:lane+4]=encoded
        for v in rows:
            if v['sequence']==r['sequence']:v['data']=data.hex()
    try:check_observations(c,rows)
    except (AssertionError,KeyError,ValueError) as e:results[fault]={'rejected':True,'reason':str(e)}
    else:raise AssertionError('Corrupted LLM observation accepted: '+fault)
# The independent reference must not leak a future token into an earlier position.
m=c['llm_model'];prefix=encode(m,c['benchmark']['prompt'])[:m['architecture']['context_length']-1]
one=forward(m,prefix+[0]);two=forward(m,prefix+[1])
assert one[:-1]==two[:-1]
results['reference_causal_mask']={'passed':True}
# Reject misleading or unbounded deployments before compilation/simulation.
from configure import load
invalid = {'empty_prompt': {'prompt': ''}, 'unknown_character': {'prompt': '你好'},
           'context_overflow': {'prompt': 'r'*16, 'generated_tokens': 2},
           'zero_tokens': {'generated_tokens': 0}, 'too_many_tokens': {'generated_tokens': 9},
           'non_boolean_cache': {'kv_cache': 1}, 'unknown_field': {'batch': 2},
           'external_model': {'model': '/tmp/not-a-project-model.json'}}
with tempfile.TemporaryDirectory(prefix='coral-llm-config-') as td:
    path=Path(td)/'benchmark.json'
    for name, changes in invalid.items():
        b=dict(c['benchmark']); b.update(changes);path.write_text(json.dumps(b))
        try:load(str(path))
        except (ValueError,AssertionError) as e:results['config_'+name]={'rejected':True,'reason':str(e)}
        else:raise AssertionError('Invalid LLM configuration accepted: '+name)
    b=dict(c['benchmark']);b.update(prompt='r'*16,generated_tokens=1)
    path.write_text(json.dumps(b));load(str(path))
    results['config_max_context_single_token']={'passed':True}
output.write_text(json.dumps({'passed':True,'checks':results},indent=2)+'\n')
print(output.read_text())
```
<!-- TINY_LLM_NEGATIVE_END -->

</details>

用第 10 步真实通过的完整原始结果作为输入：

```bash
.deps/toolchain/bin/python scripts/check_llm_negative.py results/tutorial-tiny-llm-first results/tutorial-tiny-llm-first/llm-negative.json
```

它在内存副本中分别修改权重、logits、生成 token、KV，或删去一条中间结果，并要求 `check_observations()` 拒绝。部分错误连同配对响应一起修改，所以不是只靠“请求和响应不相等”检测出来。原始 CSV 不会被改写，也不会拿这些人为错误记录作为正常计算通过的证据。

同一程序还检查参考实现的因果性：改变未来 token，前面的计算记录应保持相同。配置层检查空提示词、词表外字符、上下文越界、生成数为 0 或大于 8、非布尔缓存开关、未知字段、工程外模型路径，共 8 种非法输入。

`16` 个提示字符加 `1` 个生成 token 是合法边界，脚本会检查它能通过配置解析。**这只是配置边界检查，不是该边界场景已跑过 RTL 的声明。**

## 第 15 步：纳入统一 test，保留已有负载回归

修改 [scripts/test.py](../scripts/test.py)。在已有普通内存和 SMOKE 场景之后加入以下 LLM 场景与对照断言。该代码使用文件中已有的 `run_case()`、`invoke()`、`read()`、`cases`、`reused`、`out` 和线程池：

<!-- TINY_LLM_TEST_CASES_BEGIN -->
```python
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
```
<!-- TINY_LLM_TEST_CASES_END -->

在构造总 `result` 后、写总 `summary.json` 前追加 LLM 汇总：

<!-- TINY_LLM_TEST_RESULT_BEGIN -->
```python
result['llm'] = {'passed': True, 'cases': {k: v['llm'] for k, v in cases.items() if 'llm' in v},
                 'negative_checks': read(out / 'llm-negative.json'),
                 'memory_feedback_cycles': [cached['devices']['cycles'], llm_slow['devices']['cycles']],
                 'kv_cache_cycles': [cached['devices']['cycles'], uncached['devices']['cycles']]}
```
<!-- TINY_LLM_TEST_RESULT_END -->

还需在 `reusable()` 中加入模型匹配条件，防止更换权重后复用旧结果：

```python
model_matches = (b['name'] != 'tiny_llm' or
                 saved.get('llm_model') == build_inputs(arch, b)['model'])
```

把它与原有架构、benchmark、源码版本和产物检查一起作为返回条件。修改实现后首次验收使用新目录，不使用续跑替代新代码验收。当前完整 `test.py` 已包含上述逻辑。

之后运行唯一的完整验收入口：

```bash
./run.sh test --output results/tutorial-tiny-llm-acceptance
```

当前完成态共包含 13 个端到端场景：2 个 SMOKE、5 个普通内存、6 个 LLM；还执行 19 项 mem_sim 原生测试、在线 C ABI 检查和各类错误拒绝检查。若你从更早、尚无 SMOKE 的基线接入，LLM 接入本身增加的是这 6 个场景；当前交付的总数量则以实际 `test.py` 为准。

总 `summary.json` 的 `passed` 必须为 true；`llm.passed`、六个场景以及 `llm.negative_checks.passed` 也都必须为 true。运行耗时较长时，查看新结果目录里的场景日志和摘要；不要把程序仍在运行误判成通过。

## 第 16 步：对照已有结果，理解数字说明了什么

以下来自 **2026-09-25 已完成的 SMOKE 整理后全套验收**，当前教程只读取这些已保存的结果。归档入口为 [validation/2026-09-25-smoke/README.md](../validation/2026-09-25-smoke/README.md)，总结果为 [summary.json](../validation/2026-09-25-smoke/summary.json)。完整原始结果位于本机 `results/acceptance-smoke-20260925/`；归档中的 LLM 子目录主要保留摘要和日志。

### 16.1 六个 LLM 场景

| 场景 | 生成文本 | 前向次数 | 外部事务数 | NPU 周期 | 推理至末 token（ns） |
|---|---|---:|---:|---:|---:|
| [默认](../validation/2026-09-25-smoke/llm/summary.json) | `"blu"` | 6 | 7111 | 320502 | 549894 |
| [慢内存](../validation/2026-09-25-smoke/llm_slow/summary.json) | `"blu"` | 6 | 7111 | 461980 | 817418 |
| [CRC 重放](../validation/2026-09-25-smoke/llm_replay/summary.json) | `"blu"` | 6 | 7111 | 323741 | 554650 |
| [反压](../validation/2026-09-25-smoke/llm_backpressure/summary.json) | `"blu"` | 6 | 7111 | 247047 | 862048 |
| [关闭缓存](../validation/2026-09-25-smoke/llm_no_cache/summary.json) | `"blu"` | 15 | 15746 | 722751 | 1354394 |
| [长提示词](../validation/2026-09-25-smoke/llm_long/summary.json) | `". t"` | 9 | 10243 | 475020 | 858724 |

六个场景的数值与完整链路均通过。默认用例各阶段最大绝对误差的最大值约为 `6.98e-6`，生成 ID 为 `[4, 10, 15]`。长提示词 `"one two"` 得到 `[2, 1, 14]`，拼接为 `"one two. t"`。

默认与慢内存的计算次数、事务数和结果相同，但周期从 320502 增至 461980，说明在线内存返回时间确实反馈到 RTL 执行。不能据此声称慢内存让整次程序时间严格变为四倍。

关闭缓存后，同样输出 `"blu"`，但前向次数从 6 增至 15，事务从 7111 增至 15746，证明缓存开关实际改变了重复计算与访存。

反压用例虽然设备周期数较少，但每周期为 4 ns，默认为 2 ns；其统一推理时间反而更长。这不是“加反压让 NPU 更快”。

### 16.2 默认 7111 笔外部事务具体来自哪里

| 区域 | 写请求 | 读请求 | 含义 |
|---|---:|---:|---|
| 权重 | 1004 | 4518 | 初始化全部权重槽，然后推理读取实际需要的张量内容 |
| K cache | 176 | 168 | 128 槽清零 + 6 × 8 槽计算写入，以及历史 K 读取 |
| V cache | 176 | 168 | 与 K 对应 |
| 中间记录 | 870 | 0 | 每位置 145 个有效 float × 6 个位置 |
| token 输入区 | 6 | 6 | 4 个提示字符 + 2 个反馈输入 |
| report | 16 | 3 | 状态、阶段、调用次数、输出 token，以及输出回读 |
| 合计 | **2248** | **4863** | **7111 笔** |

计数包含初始化和观察记录。`sources.coralnpu.bytes=113776` 是 `7111 × 16` 的原生事务口径，不等于有效模型字节，也不是 Flit 总线开销。不同层次的请求、AXI beat 和 Flit 数量要按各自协议解释。

### 16.3 时间从哪里取，怎样分析

校验器以 `npu_requests.csv` 中 report 写请求的**完成响应时间**作为阶段标记。1 tick 为 1 fs，换算 ns 除以 `1e6`。

| 指标 | 定义 | 默认值 |
|---|---|---:|
| `initialization_ns` | `report[0]` 完成到 `report[1]` 完成 | 90300 ns |
| `prefill_to_first_token_ns` | 第一轮开始标记完成到首 token 写回完成 | 355662 ns |
| `decode_inter_token_ns` | 相邻生成 token 写回完成的间隔 | 95700、98532 ns |
| `inference_to_last_token_ns` | 第一轮开始标记完成到最后 token 写回完成 | 549894 ns |
| `decode_tokens_per_simulated_second` | 后续 token 数 / 首 token 到末 token 的时间 | 约 10296.96 |

这里的 prefill 顺序处理提示位置，首 token 时间包含 logits 和选择结果的写回。阶段时长与 token 间隔的边界不同，不应强行认为它们逐项相等。

模型初始化单独计时，prefill/decode 不含初始化和启动；推理时间包含逐层中间记录、标记等观察开销。总 NPU 周期还包含初始化等工作，所以不能把“推理至末 token”直接等同于“总周期 × 周期长度”。

这些是微型模型在当前 RTL、链路和内存行为模型下的仿真指标，不是宿主电脑的墙钟运行时间，也不能代表真实芯片吞吐或通用大模型性能。内存 PHY/DFI 是行为模型，HBM4 预设包含临时时序项。

## 第 17 步：修改参数和排查失败时从哪里入手

| 想修改或遇到的问题 | 应该检查的位置与动作 |
|---|---|
| 改提示词、生成数、缓存开关 | 改 `config/benchmarks/*.json`，直接运行；由运行器重建 ELF |
| 改模型权重 | 保存到 `config/llm/`，更新模型版本和 benchmark 选择，生成新验收目录 |
| 改设备/AXI/内存时钟、队列 | 改 `config/architecture.json` 或独立架构预设；不用重新生成模型 |
| 想增加层数、维度或模型类型 | 同步扩展格式校验、参考、头文件、NPU 程序、TCM/外部布局和验收；只改 JSON 会被拒绝 |
| `unknown benchmark` | tiny_llm 分支尚未加入 `configure.load()`，或名称拼错 |
| 提示词或上下文报错 | 检查词表、非空条件及 `p + g - 1 <= 16` |
| `llm_config.h` 找不到 | 检查模型模块、`--generate-kernel` 分支和源码暂存位置 |
| ELF 目标不存在或产物未复制 | 检查 BUILD 是否安装、目标是否为 `//native:tiny_llm.elf`、构建脚本是否按名称复制 |
| 指令存储超限或浮点行为异常 | 检查优化/ISA 选项、启动权重是否仍在 DTCM、栈和程序体积；不要放宽验收掩盖问题 |
| `KeyError: llm_model` | `run.py` 没有在 `resolved.json` 保存模型快照 |
| `Weight staging mismatch` | 模型快照、张量顺序、生成头文件或 ELF 不一致，先核对构建输入 |
| 某层数值误差超限 | 从最早出错的 embedding/norm/QKV 等阶段查起，再检查矩阵布局、位置、因果范围和浮点运算 |
| `Missing ... writes` | NPU 提前结束、漏写记录，或设备/校验器对计算次数和偏移的约定不一致 |
| watchdog 超时 | 查看最后请求/响应、队列和阶段标记，确认是在等待内存还是程序未结束；扩大时间上限不能代替定位原因 |
| 改 C++ 后行为仍旧 | 先 `./run.sh build`，再使用新的结果目录运行 |
| 改完配置，旧目录拒绝启动 | 这是防止覆盖证据；换一个新目录 |

新 benchmark 的交付至少包含以下内容：

| 类别 | 原始文件 |
|---|---|
| 模型与训练 | `config/llm/corpus.txt`、`training.json`、`tiny_char_v1.json`、`scripts/train_tiny_llm.py` |
| 设备负载 | `benchmarks/tiny_llm/kernel.cc`、`benchmarks/tiny_llm/README.md` |
| 模型辅助与验收 | `scripts/llm_model.py`、`verify_llm.py`、`check_llm_negative.py` |
| 负载预设 | `config/benchmarks/tiny_llm.json`、`tiny_llm_no_cache.json`、`tiny_llm_long.json` |
| 公共接入点 | `configure.py`、`build_device.sh`、`run.py`、`verify.py`、`test.py`、`integration/coralnpu/BUILD.bazel`、`run.sh` |
| 使用和证据 | 根 README/配置说明/benchmark 索引、开发教程、实际验收摘要和完整新结果目录 |

其中生成的 `llm_config.h`、暂存的 `tiny_llm.cc` 和 ELF 是构建产物；维护原始模型、配置与源码。用户最终应能用 `./run.sh llm` 执行单例，用 `./run.sh test` 验收整套计算与链路，并从保存的证据解释生成结果、缓存效果和内存反馈。

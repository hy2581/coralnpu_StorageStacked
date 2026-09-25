# 假设项目还没有 SMOKE：一步一步把它接进来

这篇教程从一个明确的起点开始：**项目已经能运行 CoralNPU、AXI/UCIe 和在线内存，也有普通内存负载和 LLM，但还没有 SMOKE 程序、SMOKE 配置、编译目标、校验器或快捷命令。**

我们的任务是亲手加上一个最小负载，直到它能从命令行启动，在 NPU 中计算，经外部内存读写，最后通过独立检查。

当前交付目录已经包含下面这些修改。阅读时可以按步骤对照完成态；如果文件里已有对应分支，不要重复添加，也不需要删除现有 SMOKE 来阅读教程。本文的命令是开发操作顺序，末尾另列此前已经完成的实际验收结果。

## 第 0 步：先让“不带 SMOKE 的项目”能运行

先进入项目根目录，阅读 [README.md](../README.md) 和 [config/README.md](../config/README.md)，确认已有的链路是：

```text
CoralNPU RTL → 原生异步接口 → AXI256 → AXI2Flit → UCIe → 在线 mem_sim
```

在没有准备过环境的项目副本中，先执行：

```bash
./run.sh setup
./run.sh build
./run.sh check
./run.sh run --output results/tutorial-baseline
```

前三条准备工具、构建公共库和运行器、检查环境。最后一条跑已有的普通内存负载，确认加 SMOKE 之前，公共链路已经能通过验收。输出目录应为新目录；若已存在，换名字。

**这一阶段的通过条件：**普通负载的 `summary.json` 为 `passed: true`，而不只是 `check` 成功。之后新增负载时，才容易判断问题出在新代码还是原有环境。

本文沿用已经整理好的目录：普通负载在 `benchmarks/memory_roundtrip/kernel.cc`，LLM 在 `benchmarks/tiny_llm/kernel.cc`。如果拿到的是两个 `.cc` 平铺在 `benchmarks/` 的旧布局，先将它们分别移动到上述位置；第 4 步会同步修改源码复制路径。空的旧 `benchmarks/llm/` 可以在确认没有文件后移除。

## 第 1 步：先定义“SMOKE 要做什么”和“什么算成功”

我们选择一个 32 位整数加一，不需要循环或模型：

```text
NPU 写入 41 → NPU 读回 41 → NPU 加一并写入 42 → NPU 读回 42
```

先约定三个地址，复用当前项目的地址窗口：

| 地址 | 用途 | 是否经过外部内存链路 |
|---|---|---|
| `0x90000000` | 输入整数 | 是 |
| `0x90010000` | 输出整数 | 是 |
| `0xc0000000` | 完成 mailbox | 属于设备控制状态，不计入外部内存事务 |

通过条件也在写代码前确定：

1. NPU 按“写输入、读输入、写输出、读输出”的顺序发出四笔外部事务。
2. 默认输入实际为 41，输出实际为 42；换输入后满足 uint32 的加一规则。
3. 写入与读取都经过既有完整链路，返回正确字节。
4. 两次写产生 AW/W/B，两次读产生 AR/R，合计 10 次握手。
5. 没有缺失、重复、提前返回或未完成事务，原始波形与日志相符。

接下来每次修改，都服务于这些条件。

## 第 2 步：新增 NPU 实际执行的程序

先建立负载目录：

```bash
mkdir -p benchmarks/smoke
```

新建 **`benchmarks/smoke/kernel.cc`**，写入完整程序：

<!-- SMOKE_KERNEL_BEGIN -->
```cpp
// Four real external transactions: write input, read input, write output, read output.
#include <cstdint>
#include "smoke_config.h"

int main() {
    auto* input = reinterpret_cast<volatile uint32_t*>(0x90000000u);
    auto* output = reinterpret_cast<volatile uint32_t*>(0x90010000u);
    auto* mailbox = reinterpret_cast<volatile uint32_t*>(0xc0000000u);

    *input = SMOKE_INPUT;
    const uint32_t value = *input;
    *output = value + 1u;
    const uint32_t observed = *output;
    const uint32_t expected = uint32_t(SMOKE_INPUT) + 1u;
    *mailbox = observed == expected ? 0x600d0000u : 0xbad00001u;
    asm volatile("wfi");
    return 0;
}
```
<!-- SMOKE_KERNEL_END -->

这里有四个关键点：

- `volatile` 要求真正访问对应地址，四次读写不会被当成普通局部变量运算消掉。
- `value` 来自外部读返回，`value + 1u` 在 NPU 中执行；宿主不代算结果。
- `expected` 从输入常量独立计算，用来与最终回读值比较。
- 正确时写 `0x600d0000`，错误时写 `0xbad00001`，再用 `wfi` 结束。完成标记之后仍要进行宿主侧独立验收。

此时 `smoke_config.h` 还不存在。这是有意的：输入参数稍后从 JSON 生成，不能在 C++ 和配置文件中分别手写一套默认值。

**本步完成后：**有了设备程序，但构建系统尚不知道如何编译它，因此还不能直接运行。

## 第 3 步：新增公开配置，并让配置解析器认识它

新建 **`config/benchmarks/smoke.json`**：

```json
{
  "name": "smoke",
  "input": 41
}
```

`name` 用来选择负载，`input` 决定第一次向外部内存写什么。配置继续统一放 `config/`；不要在 `benchmarks/smoke/` 再保存另一份默认参数。

接着修改 **`scripts/configure.py` 的 `load()`**。在判断 benchmark 名称的 `if/elif` 链中、最终“未知负载”的分支之前，加入：

```python
elif b.get('name') == 'smoke':
    keys(b, 'name input', 'benchmark')
    if type(b['input']) is not int or not 0 <= b['input'] <= 0xffffffff:
        raise ValueError('smoke input must be a uint32 integer')
```

这段代码和已有 `tiny_llm`、`memory_roundtrip` 分支同级。它同时检查字段是否完整、是否多写字段，以及输入是否为 uint32 整数。这里用 `type(...) is int`，避免把 Python 的布尔值 `True` 当成整数 1 接受。

检查解析器是否已经接上：

```bash
.deps/toolchain/bin/python scripts/configure.py \
  --benchmark-name config/benchmarks/smoke.json
```

预期只输出 `smoke`。如果仍提示 `unknown benchmark`，检查分支位置和 JSON 的 `name`。

**本步完成后：**脚本能理解新配置，但还没有把 `input` 交给设备编译器。

## 第 4 步：把 JSON 变成 C++ 头文件，并复制设备源码

还是修改 **`scripts/configure.py`**，这次找到 `main()` 中的 `if o.generate_kernel:`。

这个分支负责准备 `third_party/coralnpu/native/` 下的构建输入。把原来的源码复制语句整理成下面的形式，并在 LLM 与普通内存头文件生成逻辑之前加入 SMOKE 分支：

```python
if o.generate_kernel:
    target = ROOT / 'third_party/coralnpu/native'
    for name, staged in (('memory_roundtrip', 'ddr_touch'),
                         ('tiny_llm', 'tiny_llm'),
                         ('smoke', 'smoke')):
        shutil.copy2(ROOT / 'benchmarks' / name / 'kernel.cc',
                     target / (staged + '.cc'))
    if b['name'] == 'smoke':
        (target / 'smoke_config.h').write_text(
            '// Generated from config/benchmarks/smoke.json or the selected preset.\n'
            f'#define SMOKE_INPUT {b["input"]}u\n')
        return
```

上面是该分支的**开头部分**。后面已有的 `tiny_llm` 分支和普通内存头文件生成代码都要保留。`return` 保证 SMOKE 不会继续进入普通负载的 `words`、`iterations` 等字段处理。

注意三个名称的关系：

| 名称 | 用途 |
|---|---|
| `benchmarks/smoke/kernel.cc` | 开发者编辑的原始源码 |
| `third_party/coralnpu/native/smoke.cc` | 构建时复制出来的源码 |
| `third_party/coralnpu/native/smoke_config.h` | 从所选 JSON 生成的输入常量 |

再找到 `build_inputs()`。本次接入时把公共构建输入标记更新为：

```python
result = {'device': DEVICE, 'runtime': 'native-v3-benchmarks', 'benchmark': b}
```

保留函数已有的模型快照等后续处理。这个标记参与与 `build/device-config.json` 的比较，使接入后的首次运行重新检查并构建设备程序；它没有改变 SystemC 或硬件架构版本。

公共库已经在第 0 步构建过，现在可以单独检查生成步骤：

```bash
.deps/toolchain/bin/python scripts/configure.py \
  --generate-kernel config/benchmarks/smoke.json
cat third_party/coralnpu/native/smoke_config.h
```

预期头文件包含：

```cpp
#define SMOKE_INPUT 41u
```

这条检查只证明参数生成正确，还没有编译或运行 NPU。以后改输入应改 JSON，不应手工修改生成头文件。

## 第 5 步：告诉 Bazel 怎样编译 SMOKE

修改 **`integration/coralnpu/BUILD.bazel`**，在已有设备二进制目标旁增加：

```python
coralnpu_v2_binary(
    name="smoke",
    srcs=["smoke.cc", "smoke_config.h"],
)
```

这里使用 `coralnpu_v2_binary`，是因为程序要交叉编译给 CoralNPU 执行。不要把它当成宿主 Linux 程序用普通 `g++` 编译后运行。

`integration/coralnpu/install.sh` 会将这份 BUILD 文件复制到 `third_party/coralnpu/native/BUILD`。宏创建的设备目标名是 **`//native:smoke.elf`**。

接着检查 **`scripts/build_device.sh`** 的通用选择逻辑：

```bash
benchmark_name=$("$AXI_PYTHON" "$SS_ROOT/scripts/configure.py" --benchmark-name "${1:-config/benchmark.json}")
kernel_target=$benchmark_name
[[ $benchmark_name != memory_roundtrip ]] || kernel_target=ddr_touch
targets=("//native:$kernel_target.elf")
```

当前框架已经有这段逻辑，因此这里不需要再加一套 SMOKE 编译器调用。名称为 `smoke` 时会自动选择 `//native:smoke.elf`；脚本后续将产物保存为 `build/coralnpu/smoke.elf`。

同样，`scripts/configure.py` 中已有的 `kernel_path(b)` 返回 `build/coralnpu/<name>.elf`，也能直接选中 SMOKE。如果正在从更老的固定 ELF 入口移植，则需要先补上这两处按负载名选择的逻辑。

到这里，源码、参数和编译目标已对应起来：

```text
smoke.json
    ↓ configure.py
native/smoke.cc + native/smoke_config.h
    ↓ coralnpu_v2_binary
//native:smoke.elf
    ↓ build_device.sh
build/coralnpu/smoke.elf
```

**本步完成后：**具备构建新程序的条件。先继续接入独立校验，再启动正式验收，避免把“设备结束”误当作“负载验收通过”。

## 第 6 步：新增一个真正检查计算和链路的校验器

现有公共校验已经负责 AXI、Flit、内存、DFI 和 VCD，但它不知道 SMOKE 应当算出什么。我们需要为新负载增加自己的通过条件。

新建 **`scripts/verify_smoke.py`**，完整内容如下。代码分成两部分：`check_observations()` 检查四笔操作和返回字节；`verify_smoke()` 关联已有链路证据，生成时间表与报告。

<!-- SMOKE_VERIFIER_BEGIN -->
```python
"""Check the four observed smoke transactions and describe their real path."""
import csv
import json


def check_observations(benchmark, source):
    value = benchmark['input']
    expected = (value + 1) & 0xffffffff
    operations = [('W', 0x90000000, value), ('R', 0x90000000, value),
                  ('W', 0x90010000, expected), ('R', 0x90010000, expected)]
    assert len(source) == 8, 'SMOKE requires exactly four requests and four responses'
    observed = []
    for i, (command, address, word) in enumerate(operations):
        request, response = source[2*i:2*i+2]
        assert request['event'] == 'request' and response['event'] == 'response', 'SMOKE request/response order differs'
        assert request['sequence'] == response['sequence'], 'SMOKE response sequence differs'
        assert int(request['tick']) < int(response['tick']), 'SMOKE completed before issue'
        if i:
            assert int(source[2*i-1]['tick']) < int(request['tick']), 'SMOKE operations must be sequential'
        for row in (request, response):
            assert row['command'] == command and int(row['address']) == address, 'SMOKE operation/address differs'
            assert int(row['response']) == 0, 'SMOKE memory error'
        payload = bytes.fromhex(response['data'])
        assert len(payload) == 16 and payload[:4] == word.to_bytes(4, 'little') and payload[4:] == bytes(12), 'SMOKE observed bytes differ from independent input + 1'
        if command == 'W':
            assert int(request['mask']) == int(response['mask']) == 15, 'SMOKE write mask differs'
            assert request['data'] == response['data'], 'SMOKE write response bytes differ'
        observed.append(int.from_bytes(payload[:4], 'little'))
    assert len({r['sequence'] for r in source if r['event'] == 'request'}) == 4, 'Duplicate SMOKE request'
    return {'passed': True, 'input': observed[1], 'output': observed[3], 'expected_output': expected,
            'operation': '(input + 1) modulo 2^32', 'external_transactions': 4,
            'reads': 2, 'writes': 2, 'native_transfer_bytes': 64, 'useful_word_bytes': 16}


def verify_smoke(root, config):
    with (root / 'npu_requests.csv').open() as stream:
        source = list(csv.DictReader(stream))
    result = check_observations(config['benchmark'], source)
    journeys = json.loads((root / 'memsim_journeys.json').read_text())
    assert len(journeys) == 4
    memory = json.loads((root / 'memsim_config.json').read_text())
    timeline = []
    for i in range(4):
        request, response = source[2*i:2*i+2]
        matching = [j for j in journeys if j['axi_id'] == int(request['wire_id'])]
        assert len(matching) == 1
        journey = matching[0]
        assert journey['command'] == request['command'] and journey['address'] == int(request['address'])
        assert len(journey['children']) == 1
        child = journey['children'][0]
        ticks = {'npu_request': int(request['tick']), 'axi_address': journey['axi_tick'],
                 'forward_flit_received': max(int(p['rx_last_tick_fs']) for p in journey['forward']),
                 'memory_accept': journey['accept_tick'],
                 'dram_command': int(child['command']['cycle']) * memory['period_fs'],
                 'memory_return': journey['return_tick'],
                 'reverse_flit_received': max(int(p['rx_last_tick_fs']) for p in journey['reverse']),
                 'axi_response': max(int(p['axi_tick_fs']) for p in journey['reverse']),
                 'npu_response': int(response['tick'])}
        assert list(ticks.values()) == sorted(ticks.values()), 'SMOKE path timestamps are not causal'
        timeline.append({'step': i+1, 'command': request['command'], 'address': hex(int(request['address'])),
                         'value': int.from_bytes(bytes.fromhex(response['data'])[:4], 'little'),
                         'physical_command': child['command']['command'],
                         'time_ns': {k: v/1e6 for k,v in ticks.items()},
                         'roundtrip_ns': (int(response['tick'])-int(request['tick']))/1e6})
    result['timeline'] = timeline
    result['timing_scope'] = 'Absolute simulation time from raw source, AXI, decoded Flits and online memory evidence'
    (root / 'smoke_summary.json').write_text(json.dumps(result, indent=2)+'\n')
    report = ['# SMOKE 四笔事务', '',
              f"实际输入 {result['input']}，实际回读输出 {result['output']}，独立期望 {result['expected_output']}。",
              '完整计算与链路结论以同目录 summary.json 的 passed 为准。', '',
              '| 步骤 | 操作 | 地址 | 值 | NPU 发起 ns | 内存接收 ns | DRAM 命令 ns | 内存返回 ns | NPU 完成 ns |',
              '|---|---|---|---:|---:|---:|---:|---:|---:|']
    for t in timeline:
        ns = t['time_ns']
        report.append(f"| {t['step']} | {t['command']} | {t['address']} | {t['value']} | {ns['npu_request']:.3f} | {ns['memory_accept']:.3f} | {ns['dram_command']:.3f} | {ns['memory_return']:.3f} | {ns['npu_response']:.3f} |")
    report += ['', '每次访问包含 4 字节有效计算数据；原生接口传输 16 字节，后级 AXI 为 256 bit。',
               '上表为绝对仿真时刻，不是宿主运行耗时。Flit 收发与 AXI 时刻详见 smoke_summary.json。',
               '原始证据在 npu_requests.csv、axi_wave.vcd、ucie_flits.csv、memsim_bridge.csv 等文件。', '']
    (root / 'smoke_report.md').write_text('\n'.join(report))
    return result
```
<!-- SMOKE_VERIFIER_END -->

阅读这段代码时，重点看以下关系：

- 期望值由当次配置的 `input` 加一得到，最终 `output` 从真实读返回中提取。
- 四笔请求各有一个响应，所以默认要求 8 行源事件，并严格限制顺序、地址与值。
- 写掩码 `15` 即 `0x000f`，只写低四字节；原生 beat 为 16 字节。
- `memsim_journeys.json` 是公共检查器从 AXI、Flit 和内存证据关联得到的结果，不是手工编造的时间表。
- 输出 `smoke_summary.json` 便于脚本读取，`smoke_report.md` 便于人阅读。综合结论仍要写进公共 `summary.json`。

## 第 7 步：把新校验器接到公共验收入口

仅有 `verify_smoke.py`，运行器不会自动调用它。还要修改 **`scripts/verify.py`** 的两个位置。

**第一处：**找到完成状态和周期检查之后、按 benchmark 名称选择算法检查的地方。在已有 LLM 分支与普通内存分支之间加入：

```python
elif b['name'] == 'smoke':
    from verify_smoke import verify_smoke
    smoke = verify_smoke(root, c)
    devices.update(input=smoke['input'], output=smoke['output'],
                   mailbox=completion['mailbox'])
```

后面的普通内存 `else` 分支应明确限制负载名，避免未知负载误入普通乘加验收：

```python
assert b['name'] == 'memory_roundtrip', 'Unknown benchmark in saved evidence'
```

这句放在该 `else` 分支的开头，原有普通内存检查继续保留。

**第二处：**找到公共 `result` 字典已创建、`summary.json` 尚未写入的位置，加入：

```python
if b['name'] == 'smoke':
    result['smoke'] = smoke
```

不要跳过函数前面的公共检查。正确的调用顺序仍然是：

```text
validate.py
  → AXI 检查
  → AXI2Flit/UCIe 检查与原始 Flit 解码
  → 在线内存、DRAM/DFI、最终镜像检查
  → 原始 VCD 五通道审计
  → verify.py 公共源请求/回调与完成状态检查
  → verify_smoke() 四笔事务、计算结果和时间表
  → 综合 summary.json
```

`scripts/validate.py` 原本已经调用 `scripts/verify.py`，所以无需另加一条绕过公共检查的 SMOKE 运行路径。

**本步完成后：**已有通用 `run --benchmark ...` 可以构建、运行和验收 SMOKE。

## 第 8 步：第一次运行，先用已有的通用命令

先重建，再用现有入口选择新的配置。此时还不需要 `run.sh smoke` 快捷命令：

```bash
./run.sh build
./run.sh run --benchmark config/benchmarks/smoke.json \
  --output results/tutorial-smoke-first
```

`build` 构建公共库与默认负载；通用 `run` 发现所选配置是 SMOKE，会调用 `build_device.sh` 自动编译对应 ELF，然后启动现有 SystemC 运行器。

本次运行会依次产生：

| 文件 | 出现阶段 | 回答的问题 |
|---|---|---|
| `benchmark-build.log` | 配置切换并触发编译时 | 设备程序是否成功编译 |
| `resolved.json` | 启动前 | 是否选中了 smoke、输入 41 和正确 ELF |
| `environment.json` | 启动前 | 实际使用了哪些版本与本项目产物 |
| `run.log`、`completion.json` | 仿真执行后 | NPU 是否结束、多少周期、mailbox 是什么 |
| `npu_requests.csv`、AXI/Flit/内存/VCD | 仿真过程中 | 四笔访问是否实际发生并返回 |
| `verification.log` | 事后验收 | 各类检查是否通过 |
| `smoke_summary.json`、`smoke_report.md` | SMOKE 校验后 | 实际结果和逐笔链路时间 |
| `summary.json` | 综合验收后 | 最终是否为 `passed: true` |

首次运行成功后，可以读取核心结果：

```bash
.deps/toolchain/bin/python - <<'PY'
import json
from pathlib import Path
p = Path('results/tutorial-smoke-first')
s = json.loads((p / 'summary.json').read_text())
assert s['passed']
print('input/output:', s['smoke']['input'], s['smoke']['output'])
print('transactions:', s['smoke']['external_transactions'])
print('AXI handshakes:', s['checks']['check_summary']['axi_handshakes'])
print('NPU cycles:', s['devices']['cycles'])
PY
```

默认配置应看到输入 41、输出 42、四笔事务、10 次握手。周期值要由实际结果读取，不能因为之前某次是 293 就硬编码到校验器里。

## 第 9 步：让使用者可以直接执行 `./run.sh smoke`

到这一步，功能已经通过通用入口接上。再修改 **`run.sh`** 的 `case "$command" in`，增加分支：

```bash
smoke)
    source "$root/scripts/activate.sh"
    exec "$AXI_PYTHON" "$root/scripts/run.py" \
        --benchmark config/benchmarks/smoke.json "$@" ;;
```

并将用法提示中的命令列表更新为：

```text
setup|build|run|smoke|llm|test|check|validate
```

这个分支只是给现有通用入口一个简短名称。保留 `"$@"` 后，`--output`、`--scale`、`--architecture` 和自定义 `--benchmark` 仍会传入同一个运行器。

再修改 **`scripts/run.py`**，在调用 `validate.py` 成功返回后、最后打印 `PASS` 之前加入以下片段，让 SMOKE 在终端显示关键结果：

```python
if b['name'] == 'smoke':
    result = json.loads((output / 'summary.json').read_text())
    smoke = result['smoke']
    print(f"SMOKE: {smoke['input']} + 1 = {smoke['output']} (uint32); "
          f"{smoke['external_transactions']} external transactions; "
          f"{result['checks']['check_summary']['axi_handshakes']} AXI handshakes; "
          f"{result['devices']['cycles']} NPU cycles")
    print('Report: ' + str(output / 'smoke_report.md'))
```

这些数字从已验收的结果中读取。不要在仿真启动前就打印“成功”，也不要把完成 mailbox 当作最终通过条件。

现在验证快捷入口：

```bash
./run.sh smoke --output results/tutorial-smoke-shortcut
```

在本项目已验收的默认配置下，输出包含：

```text
SMOKE: 41 + 1 = 42 (uint32); 4 external transactions; 10 AXI handshakes; 293 NPU cycles
```

后面会打印报告和综合结果的绝对路径。

## 第 10 步：改变内存时间，确认链路真的反馈到 NPU

保持程序和输入不变，只放慢内存：

```bash
./run.sh smoke --scale 4 --output results/tutorial-smoke-slow
```

比较默认与慢内存目录中的 `summary.json`、`smoke_summary.json`、`memsim_config.json`。应该检查：

- 两次都是输入 41、输出 42，且同样只有四笔外部事务。
- 实际内存 tick 周期变成四倍。
- 在当前验收配置下，设备周期和平均往返时间增加。

为什么这一步重要：如果存储访问只是事后写一份日志，改变内存时间就不会改变 NPU 的真实等待。这里的 `CompleteRead/CompleteWrite` 由在线返回触发，因此内存调度能够影响执行周期。

四笔访问很少，刷新和调度所处的时刻会显著影响单笔延迟。不要要求每笔时间都恰好乘四，也不要用它测饱和带宽。

## 第 11 步：故意弄错证据，检查“错误会不会被拒绝”

正确结果能通过之后，还要确认校验器不会接受错误。新建 **`scripts/check_smoke_negative.py`**，完整内容如下：

<!-- SMOKE_NEGATIVE_BEGIN -->
```python
#!/usr/bin/env python3
"""Require smoke verification to reject wrong computation and missing/reordered work."""
import copy
import csv
import json
from pathlib import Path
import sys
import tempfile
from configure import load
from verify_smoke import check_observations

source, output = map(Path, sys.argv[1:])
config = json.loads((source/'resolved.json').read_text())['benchmark']
with (source/'npu_requests.csv').open() as stream:
    original = list(csv.DictReader(stream))
assert check_observations(config, original)['passed']
checks = {}
for fault in ('wrong_input', 'wrong_output', 'wrong_readback', 'missing_transaction', 'extra_transaction', 'reordered_transactions'):
    rows = copy.deepcopy(original)
    if fault.startswith('wrong_'):
        index = {'wrong_input':0, 'wrong_output':4, 'wrong_readback':6}[fault]
        for row in rows[index:index+2]:
            if row['data']:
                data = bytearray.fromhex(row['data']); data[0] ^= 1; row['data'] = data.hex()
    elif fault == 'missing_transaction': rows = rows[:-2]
    elif fault == 'extra_transaction': rows += rows[-2:]
    else: rows = rows[2:4] + rows[:2] + rows[4:]
    try: check_observations(config, rows)
    except AssertionError as error: checks[fault] = {'rejected': True, 'reason': str(error)}
    else: raise AssertionError('Corrupted smoke evidence accepted: '+fault)
with tempfile.TemporaryDirectory(prefix='smoke-config-') as temporary:
    path = Path(temporary)/'benchmark.json'
    for label, replacement in [('negative',-1), ('overflow',2**32), ('boolean',True)]:
        path.write_text(json.dumps({'name':'smoke', 'input':replacement}))
        try: load(path)
        except ValueError as error: checks['config_'+label] = {'rejected':True, 'reason':str(error)}
        else: raise AssertionError('Bad smoke configuration accepted: '+label)
    for value in (0, 0xffffffff):
        path.write_text(json.dumps({'name':'smoke', 'input':value})); load(path)
    checks['config_uint32_boundaries'] = {'passed':True}
result = {'passed':True, 'checks':checks}
output.write_text(json.dumps(result,indent=2)+'\n')
print(output.read_text())
```
<!-- SMOKE_NEGATIVE_END -->

它读取已通过的实际运行证据，在副本上注入六种错误，不修改原始目录。写数据错误会同时改请求与响应，避免仅因两者不一致就被公共配对检查拒绝；SMOKE 自己的独立计算规则也必须能发现错误。

执行：

```bash
.deps/toolchain/bin/python scripts/check_smoke_negative.py \
  results/tutorial-smoke-shortcut results/tutorial-smoke-shortcut/smoke-negative.json
```

通过条件是六种错误证据全部被拒绝，三种非法输入配置被拒绝，uint32 两端合法配置通过校验。最后一项只是参数边界检查，不是又对边界值执行了一次 RTL 仿真。

## 第 12 步：把 SMOKE 放进 `./run.sh test`

修改 **`scripts/test.py`**。在已有环境、原生测试和在线 C ABI 检查之后，找到 `cases = {}`、`reused = []`，在后面、原有普通内存场景之前加入：

```python
for case in [('smoke', ['--benchmark', 'config/benchmarks/smoke.json']),
             ('smoke_slow', ['--benchmark', 'config/benchmarks/smoke.json', '--scale', '4'])]:
    name, result, was_reused = run_case(case)
    cases[name] = result
    if was_reused:
        reused.append(name)
invoke([sys.executable, ROOT / 'scripts/check_smoke_negative.py',
        out / 'smoke', out / 'smoke-negative.json'], 'smoke-negative.log')
assert cases['smoke']['smoke']['output'] == cases['smoke_slow']['smoke']['output']
assert cases['smoke_slow']['devices']['cycles'] > cases['smoke']['devices']['cycles']
assert (cases['smoke_slow']['sources'][DEVICE]['roundtrip_mean_ns']
        > cases['smoke']['sources'][DEVICE]['roundtrip_mean_ns'])
assert (read(out / 'smoke_slow/memsim_config.json')['period_fs']
        == 4 * read(out / 'smoke/memsim_config.json')['period_fs'])
```

这些语句放在已有的 `try:` 内，缩进与其他场景保持一致。`run_case`、`invoke`、`read` 都是已有函数，不再写另一套子进程和目录管理。

再找到总 `result` 已创建、最终 `summary.json` 写入之前的位置，加入：

```python
result['smoke'] = {
    'passed': True,
    'cases': {k: cases[k]['smoke'] for k in ('smoke', 'smoke_slow')},
    'negative_checks': read(out / 'smoke-negative.json'),
    'memory_feedback_cycles': [cases[k]['devices']['cycles']
                               for k in ('smoke', 'smoke_slow')],
}
```

这里写 `passed: True` 的前提，是前面的用例运行、独立验收与断言都已经成功。任一步异常都会进入现有失败处理，不会到达成功汇总。

执行完整回归：

```bash
./run.sh test --output results/tutorial-smoke-regression
```

这样 SMOKE 就成为后续维护的一部分。以后修改目录、运行器或内存适配，不仅会测试 LLM 和普通内存，也会先验证这四笔最简单的访问。

## 第 13 步：补上负载说明和教程入口

在 `benchmarks/smoke/README.md` 写明用途、运行命令、配置路径和成功条件；在 `benchmarks/README.md` 的索引中加入 SMOKE；在项目 `README.md` 与 `config/README.md` 加入入口和参数说明。

不把生成头文件或运行结果当成源码。开发者编辑 `benchmarks/smoke/kernel.cc` 和 `config/benchmarks/smoke.json`，构建产物由脚本生成，每次验收使用新的结果目录。

至此，需要新增或修改的文件是：

| 动作 | 文件 | 接入的环节 |
|---|---|---|
| 新增 | `benchmarks/smoke/kernel.cc` | NPU 真正执行的负载 |
| 新增 | `config/benchmarks/smoke.json` | 默认参数 |
| 修改 | `scripts/configure.py` | 配置校验、源码复制、头文件生成、构建输入标记 |
| 修改 | `integration/coralnpu/BUILD.bazel` | 交叉编译目标 |
| 新增 | `scripts/verify_smoke.py` | 独立计算校验、链路时间表和简报 |
| 修改 | `scripts/verify.py` | 接入公共验收与综合结果 |
| 修改 | `run.sh` | 增加快捷命令 |
| 修改 | `scripts/run.py` | 从验收结果打印 SMOKE 摘要 |
| 新增 | `scripts/check_smoke_negative.py` | 错误证据和参数负例 |
| 修改 | `scripts/test.py` | 默认/慢内存回归、负例与汇总 |
| 新增/更新 | 各 README 和 `docs/` | 使用入口与开发说明 |

`scripts/build_device.sh`、`kernel_path()` 和 `scripts/validate.py` 已有的通用选择与调用逻辑可以复用。CoralNPU 驱动、AXI/UCIe、内存桥和 SystemC 拓扑也直接复用；本次加入的是新设备程序及其验收规则。

## 第 14 步：最后确认它“真的跑通了”

一次完整执行可以沿下面的线索检查：

```text
JSON 的 input=41
  → 生成 SMOKE_INPUT=41u
  → 编译 smoke.elf
  → ELF 装载到本地 TCM，RTL 启动
  → NPU 发出写 41、读 41、写 42、读 42
  → 原生异步接口适配到 AXI 五通道
  → AXI2Flit 编码，经双向 UCIe 交付
  → 在线 mem_sim 实际执行命令和字节读写
  → 完成/读数据沿原路径返回 NPU
  → mailbox 完成
  → 公共链路检查 + SMOKE 独立校验
  → summary.json: passed=true
```

此前按上述接入逻辑完成的实际验收结果如下。它们来自保存的运行证据；本文新增教程没有重新运行整套硬件仿真。

| 用例 | 输出 | NPU 周期 | 外部事务 | AXI 握手 | 结果 |
|---|---:|---:|---:|---:|---|
| 默认 SMOKE | 42 | 293 | 4 | 10 | PASS |
| SMOKE，内存 scale=4 | 42 | 821 | 4 | 10 | PASS |
| 最终快捷命令独立复验 | 42 | 293 | 4 | 10 | PASS |

完整回归为 13 个新跑场景：2 个 SMOKE、5 个普通内存、6 个 LLM。19 项原生测试、在线接口与各类负例检查通过。

可以对照 [完整验收汇总](../validation/2026-09-25-smoke/summary.json)、
[默认 SMOKE 原始证据目录](../validation/2026-09-25-smoke/smoke/) 和
[最终命令输出](../validation/2026-09-25-smoke/standalone/console.log)。
四笔事务的实际链路时刻、Flit/内存记录解释及性能边界，在 [SMOKE 全流程与结果分析](04-SMOKE全流程与结果分析.md) 中详细展开。

## 按开发阶段定位常见遗漏

| 卡在哪里 | 优先检查哪一步 |
|---|---|
| `unknown benchmark` | 第 3 步：是否注册 `smoke` 配置分支 |
| 缺少 `smoke_config.h` | 第 4 步：是否生成头文件，是否选择了正确 JSON |
| 找不到 `//native:smoke.elf` | 第 5 步：BUILD 是否增加目标，是否经安装脚本更新构建副本 |
| 寻找旧的平铺 `.cc` 路径 | 第 4 步：源码复制是否改为 `benchmarks/<name>/kernel.cc` |
| NPU 已完成，但 Python 报缺少 `words` | 第 7 步：SMOKE 是否误入普通内存校验分支 |
| 只有完成状态，没有计算和链路通过结论 | 第 6～7 步：是否真正接上独立验收，是否保留公共检查 |
| `run --benchmark ...` 成功，`smoke` 命令失败 | 第 9 步：shell 分支、环境加载和参数转发 |
| 单例成功，但 `test` 没有 SMOKE | 第 12 步：是否注册两个场景及总汇总 |
| 修改 C++ 后行为没变 | 执行 `./run.sh build` 后重跑；仅修改 JSON 才由运行器自动触发参数重编译 |
| 输出目录已存在 | 换一个新目录，保留已有证据 |

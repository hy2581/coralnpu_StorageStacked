# 当前 LLM benchmark 的部署、计算和验收

当前已经接入一个可以完整运行的微型字符级 Transformer。它使用本项目小语料训练得到的权重，在 CoralNPU RTL 中完成输入处理、因果注意力、前馈网络、KV cache 和逐 token 生成；外部访问继续经过 AXI256、AXI2Flit、UCIe 与在线 mem_sim。

这是一套覆盖语言模型推理流程的功能 benchmark。内置模型只有 **1001 个参数**，适合让 RTL 仿真跑完所有计算与链路检查；它的语言能力只限于小训练语料，不能代表通用大模型质量。

## 1. 最简单的部署方法

在本项目根目录执行：

```bash
# 新环境只需准备一次；已准备好可跳过 setup
./run.sh setup
./run.sh build

# 使用随源码提供的训练权重运行完整推理
./run.sh llm

# 原有内存自检和全部 LLM 场景的完整验收
./run.sh test
```

普通构建和推理不需要安装 NumPy、PyTorch、下载外部模型或运行训练。权重、词表、模型结构和训练记录都保存在 [config/llm/tiny_char_v1.json](../config/llm/tiny_char_v1.json)。

`llm` 是下面这条命令的快捷入口，使用同一个运行器和生产链路：

```bash
./run.sh run --benchmark config/benchmarks/tiny_llm.json
```

默认输入为 `"red "`，固定生成 3 个字符 token，参考结果为 `"blu"`，拼接后得到 `"red blu"`。生成数量由配置固定，不会因为遇到某个字符而提前停止。

每次生成新的 `results/时间戳/` 目录。首先查看 `summary.json` 的 `passed`，再打开 `llm_report.md` 看简明结果。完整数值与计时在 `llm_summary.json`。

## 2. 模型具体包含什么

| 项目 | 当前实现 |
|---|---|
| 类型 | 仅解码器的自回归字符模型，batch=1 |
| 分词 | 一个字符对应一个 token，词表共 17 个字符 |
| 模型层数 | 1 层 Transformer |
| 状态维度 | 8 |
| 注意力 | 2 个头，每头 4 维，缩放点积、因果遮罩、softmax |
| 位置表示 | 学习得到的位置嵌入 |
| 归一化 | 注意力和 FFN 前各一次 LayerNorm，输出前再一次 LayerNorm |
| 前馈网络 | 8 → 16 → 8，ReLU 激活 |
| 残差 | 注意力子层与前馈子层均保留残差相加 |
| 输出 | 线性层给出 17 个 logits，取最大值对应的 token |
| 数值 | 标量 FP32，RVV 关闭 |
| 上下文 | 最多处理 16 个位置 |
| 权重 | 本地训练所得，版本 `tiny-char-trained-v1` |

logits 是各个候选字符的分数。贪心生成直接选择分数最大的字符；输出端无需再计算一次 softmax，注意力内部仍会计算 softmax。

注意力的计算定义可参阅原论文 [Attention Is All You Need](https://arxiv.org/abs/1706.03762)。本 benchmark 采用上述明确列出的微型 decoder 结构，不包含编码器或编码器到解码器的交叉注意力。

## 3. Prefill 和 decode 各做什么

**Prefill** 处理提示词。默认提示词有 4 个字符，程序依次计算这 4 个位置，把各位置的 K、V 存入外部内存。最后一个提示位置的 logits 用于生成第一个新 token。

**Decode** 把刚生成的 token 作为下一个输入。开启 KV cache 时，程序只计算这个新位置的 Q、K、V，读取历史 K/V 参与注意力，生成下一个 token。历史位置不会重新执行整层计算。

因此，默认处理 4 个提示位置并生成 3 个 token，只需要计算 `4 + 3 - 1 = 6` 个位置。最后生成的 token 已经是结果，不再送回模型。

当前 prefill 按位置顺序执行，以保持程序和工作存储简单；没有实现批量并行 prefill。这个选择影响性能，但因果注意力仍访问该位置之前的完整前缀。

关闭 KV cache 时，每次生成都会重新计算整个前缀。默认用例的前向位置计算次数变成 `4 + 5 + 6 = 15`，生成结果应保持相同。这个对照用于验证缓存确实减少重复计算与访存。

## 4. 计算在哪里，数据放在哪里

```text
Python 读取配置、准备 ELF、启动仿真
                   ↓
SystemC 统一推进设备、AXI、链路和内存时间
                   ↓
CoralNPU RTL 执行 tiny_llm.elf
  词嵌入 + 位置嵌入
  → LayerNorm → Q/K/V 投影
  → 读取历史 KV → 因果注意力与 softmax
  → 输出投影与残差
  → LayerNorm → FFN/ReLU 与残差
  → 最终 LayerNorm → logits → 选择下一个 token
                   ⇅ 外部读写
原生接口 → AXI256 → AXI2Flit → UCIe → 在线 mem_sim
```

矩阵乘加、归一化、softmax 和 token 选择都在被模拟的 CoralNPU 核中执行。Python 只做部署与事后独立校验，mem_sim 负责保存字节和建模存储访问时序。

| 地址/存储 | 放置内容 |
|---|---|
| 本地 TCM | 设备程序、启动权重镜像、当前位置的临时激活与栈 |
| `0x90000000` 起 | 推理使用的外部权重 |
| `0x90010000` 起 | K cache |
| `0x90020000` 起 | V cache |
| `0x90030000` 起 | 各位置、各计算阶段的中间结果及 logits |
| `0x90040000` 起 | 提示词和送回模型的生成 token |
| `0x90050000` 起 | 生成结果、阶段计时标记、计算次数和错误状态 |
| `0xc0000000` | 设备完成 mailbox |

设备先把本地启动镜像中的权重经完整链路写入外部内存，再开始推理。推理中的权重和 KV 访问采用实际外部读写；初始化由 NPU 发起，没有宿主直接预填外部内存的旁路。临时激活保存在 TCM，中间结果另外写到外部记录区，供逐层核对。

程序、模型装载、地址映射均使用当前项目的独立源码与构建产物，没有重新引入 gem5。

## 5. 相关源码在哪里

| 位置 | 职责 |
|---|---|
| [benchmarks/tiny_llm/kernel.cc](../benchmarks/tiny_llm/kernel.cc) | 在 NPU 上执行完整前向、缓存读写、贪心生成和阶段标记 |
| [config/benchmarks/tiny_llm.json](../config/benchmarks/tiny_llm.json) | 选择模型、提示词、生成数量和 KV cache 开关 |
| [config/llm/tiny_char_v1.json](../config/llm/tiny_char_v1.json) | 权重、词表、模型结构、版本及训练记录 |
| [scripts/configure.py](../scripts/configure.py) | 校验配置，选择 benchmark，检查是否需要重编译 |
| [scripts/llm_model.py](../scripts/llm_model.py) | 生成设备配置头文件，并提供独立的全前缀参考实现 |
| [scripts/build_device.sh](../scripts/build_device.sh) | 构建 `tiny_llm.elf`，放到 `build/coralnpu/` |
| [integration/coralnpu/coralnpu_native.cc](../integration/coralnpu/coralnpu_native.cc) | ELF 装载、NPU 启动、步进与异步完成反馈 |
| [simulation/axi_master.cc](../simulation/axi_master.cc) | 原生请求到 AXI256 的适配，保存 NPU 请求/响应证据 |
| [scripts/verify_llm.py](../scripts/verify_llm.py) | 对照独立参考逐层检查结果、缓存、token、计时与外部访问 |
| [scripts/check_llm_negative.py](../scripts/check_llm_negative.py) | 确认错误权重、logits、token、KV 和缺失结果会被拒绝 |

生成的 `third_party/coralnpu/native/llm_config.h` 来自模型和 benchmark 配置。请修改上述原始文件，不要直接改生成头文件或复制过去的设备程序。

## 6. 怎样修改负载

默认配置：

```json
{
  "name": "tiny_llm",
  "model": "config/llm/tiny_char_v1.json",
  "prompt": "red ",
  "generated_tokens": 3,
  "kv_cache": true
}
```

`prompt` 必须非空，并只包含模型词表中的字符；该词表来自项目语料。`generated_tokens` 范围为 1～8；必须满足 `字符数(prompt) + generated_tokens - 1 <= 16`。

修改配置后直接运行，运行器会自动重编译设备 ELF。改变 NPU、AXI 和在线内存参数仍使用 `config/architecture.json` 或 `--architecture`；模型结构目前固定，修改维度或层数需同步扩展源码与验收。

```bash
# 放慢内存，检查等待是否反馈到实际推理
./run.sh llm --scale 4

# 链路错误重放与推理数据检查
./run.sh llm --replay

# 小队列、单在途请求与不同时钟
./run.sh llm --architecture config/architectures/backpressure.json

# 同一模型和提示词，关闭 KV cache 作对照
./run.sh llm --benchmark config/benchmarks/tiny_llm_no_cache.json

# 较长提示词 one two
./run.sh llm --benchmark config/benchmarks/tiny_llm_long.json
```

如需保留自定义配置，可复制一份到 `config/benchmarks/` 再用 `--benchmark` 选择。参数错误会直接失败；输出目录必须新建，避免覆盖旧证据。

## 7. 检查什么才算通过

运行器先核对原有 AXI 五通道、Flit、内存请求/响应、DRAM/DFI、最终镜像与 VCD，再检查语言模型结果。设备报告完成或程序退出成功都不能代替这些检查。

LLM 检查包含：

- 初始化权重是否与选定模型的 FP32 字节完全一致，每个权重张量是否实际发生外部读取。
- 每个处理位置的嵌入、LayerNorm、Q/K/V、注意力概率、注意力输出、残差、FFN、最终状态与 logits 是否正确。
- KV cache 的初始化、各位置写入次数和数据是否正确；开启/关闭缓存时是否符合各自计算次数。
- 生成 token 是否与独立参考相同，下一步输入是否使用真实写回的数据。
- 所有预期记录是否出现，事务是否排空，错误响应或非有限数值是否被拒绝。

参考实现在宿主使用独立的 Python 浮点运算，按完整前缀计算因果注意力，不使用设备的增量缓存代码，也不把正确输出嵌入设备程序。FP32 数值要求满足 `绝对误差 <= 2e-5 + 2e-5 × |参考值|`，token ID 必须完全相同。

`./run.sh test` 包含默认、慢内存、CRC 重放、反压、关闭 KV cache、较长提示词六个 LLM 场景，还保留原有内存自检、19 项内存原生测试与在线 C ABI 检查，并加入两个最小 SMOKE 场景。LLM 额外注入五种错误观察值，要求检查器全部拒绝，并检查参考实现的因果遮罩。配置检查还覆盖八种非法参数，以及最大上下文/单 token 的合法边界；配置检查不等同于该边界的 RTL 运行验收。

## 8. 时间和吞吐怎样解释

`llm_summary.json` 中的时间来自 NPU 自己发出的阶段标记和 token 写回的真实响应，使用统一仿真时间：

| 指标 | 统计范围 |
|---|---|
| `initialization_ns` | 权重、缓存清零和输入初始化；单独报告 |
| `prefill_to_first_token_ns` | 推理开始到首个生成 token 写回完成 |
| `decode_inter_token_ns` | 相邻生成 token 写回完成之间的间隔 |
| `decode_tokens_per_simulated_second` | 后续 token 数除以首 token 到末 token 的仿真时间；仅生成一个时为空 |
| `inference_to_last_token_ns` | 推理开始到末 token 写回完成 |
| `forward_calls` | 实际执行了多少次“一个位置的完整前向” |

推理时间包含中间结果和标记的记录开销，prefill/decode 不包含模型初始化。它们不是宿主电脑的运行秒数，也不是量产芯片吞吐。内置模型训练集很小，训练损失不能替代真实语言任务准确率；这里只验收明确列出的模型与配置。

## 9. 怎样重现训练

训练使用本项目自写的 NumPy 实现和 [corpus.txt](../config/llm/corpus.txt)，配置在 [training.json](../config/llm/training.json)。没有从另一项目加载模型。训练代码为 [scripts/train_tiny_llm.py](../scripts/train_tiny_llm.py)，记录随机种子、步数、NumPy 版本及训练损失。

需要重新训练时，可建立独立的可选工具环境：

```bash
.deps/toolchain/bin/python -m venv .deps/llm-training
.deps/llm-training/bin/python -m pip install numpy==2.2.6
.deps/llm-training/bin/python scripts/train_tiny_llm.py --output build/retrained-tiny-char.json
```

这个命令把新模型写到 `build/` 供检查，不会替换默认权重。部署新模型时，把它保存到 `config/llm/`、设置合适的版本名、通过 benchmark 的 `model` 字段选择，再生成新的验收目录。当前导出模型经过 1500 步训练，训练交叉熵由约 3.02 降至约 0.172；未报告独立测试集语言质量。

## 10. 到哪里看结果

新的结果目录中，`llm_report.md` 适合快速阅读，`llm_summary.json` 适合脚本比较。原始证据仍保留在 `npu_requests.csv`、`axi_events.csv`、`axi_wave.vcd`、`ucie_flits.csv`、`memsim_bridge.csv`、`memsim_image.csv` 等文件中。

`memsim_view.html` 可以按请求查看链路和数据。在项目根目录启动 `python3 -m http.server 8000`，通过浏览器访问对应结果目录；远程机器可用端口转发。复制页面时需要连同旁边的分块数据目录一起复制。

实际完成的验收记录与范围见 [ACCEPTANCE.md](../ACCEPTANCE.md)。

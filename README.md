# coralnpu_StorageStacked

CoralNPU 的 Verilator RTL 模型自行启动 RISC-V 内核，直接写入、读取、计算并回读验证外部内存；不创建主机 CPU，不需要主机轮询。独立 SystemC 负责统一调度，不再构建或运行 gem5。

```text
CoralNPU RTL（自动启动）
  → 有界原生异步接口 → AXI128/256 字节通道适配 → AXI256 五通道
  → AXI2Flit → 双向 UCIe → mem_sim 控制器 / 行为级 PHY
  ← 沿原路径返回真实读数据和写完成
```

## 从 GitHub 获取

```bash
git clone https://github.com/hy2581/axi_StorageStacked.git
git clone https://github.com/hy2581/coralnpu_StorageStacked.git
cd coralnpu_StorageStacked
./run.sh setup
./run.sh build
./run.sh run
```

驱动源码直接保存在本仓库，包含 `third_party/coralnpu`。独立 Accellera SystemC、AXI2Flit/UCIe/mem_sim 位于独立的 `axi_StorageStacked` 仓库；不使用 submodule。
`setup` 只检查源码快照并准备工具依赖，不递归拉取 Git 仓库。
Git 保存源码、配置、补丁、许可证和 `validation/` 验收摘要；本机工具链、编译缓存和完整波形由构建/运行生成。

## 在本机运行

```bash
cd /home/hy258/hy/zhongxing/coralnpu_StorageStacked
./run.sh check
./run.sh run
```

`check` 检查环境、动态库、设备隔离；`run` 才会执行 benchmark 并校验数据与完整链路。
结果默认写入新的 `results/时间戳/`，禁止覆盖已有目录。
成功以 `summary.json` 的 `passed: true` 为准，失败保留日志和 `passed: false`。

## 配置和重新构建

中文入门说明：

- [对 CoralNPU 做了哪些修改](docs/01-CoralNPU改动说明.md)
- [如何修改参数、配置和 Benchmark](docs/02-参数配置与Benchmark使用.md)
- [当前 LLM 负载做了什么，在哪里做的](docs/03-当前LLM负载与执行位置.md)：微型字符级 Transformer 的部署、完整推理、KV cache 对照与验收。
- [SMOKE 从项目准备到结果分析](docs/04-SMOKE全流程与结果分析.md)：用四笔真实外部事务看懂整条链路。
- [从零接入 SMOKE 开发教程](docs/05-从零接入SMOKE开发教程.md)：假设原来没有 SMOKE，逐步增加程序、配置、编译目标、校验器和运行入口。
- [从零接入 tiny_llm 开发教程](docs/06-从零接入tiny_llm开发教程.md)：从模型、NPU 程序和内存布局开始，逐步接入构建、推理、独立校验与完整验收。

统一配置目录为 [`config/`](config/README.md)，日常修改两个文件：

- [`config/architecture.json`](config/architecture.json)：计算架构、时钟、AXI/UCIe、在线内存。
- [`config/benchmark.json`](config/benchmark.json)：benchmark 名称、规模和参数。

```bash
# 新环境：只准备本工程需要的固定版本依赖
./run.sh setup
# 首次或修改 C++ / RTL / 适配源码后重新构建
./run.sh build
# 运行并校验；benchmark 参数改变时自动重建设备 benchmark
./run.sh run --output results/my-case
# 最小冒烟：NPU 写 41、读 41、加一写 42、读 42 校验
./run.sh smoke
# 选择另一份 benchmark 配置
./run.sh run --benchmark config/benchmarks/strided.json
# 小型已训练语言模型：CoralNPU 执行 prefill、KV cache 和逐 token 生成
./run.sh llm
# 同一模型关闭 KV cache，比较重复计算与访存
./run.sh llm --benchmark config/benchmarks/tiny_llm_no_cache.json
# 内存延迟与重放实验
./run.sh run --scale 4
./run.sh run --replay
# 完整验收：两个 SMOKE、五个内存自检、六个 LLM 场景及错误证据拒绝
./run.sh test
```

所有路径参数均可使用绝对路径；benchmark 的相对路径相对于本工程根目录，
`--output` 的相对路径相对于调用命令时的工作目录。

`smoke` 等价于 `run --benchmark config/benchmarks/smoke.json`，使用同一条 RTL 到在线内存链路，
默认仅做 `41 + 1 = 42`。四笔外部事务会覆盖 AXI 五通道，结果目录提供 `smoke_report.md` 和逐笔链路时间表 `smoke_summary.json`。

`llm` 等价于 `run --benchmark config/benchmarks/tiny_llm.json`，使用同一条 RTL 到在线内存链路。
内置模型有 1001 个参数，采用 1 层、2 个注意力头、8 维隐藏状态、17 字符词表和 16 位置上下文。
模型权重随源码保存在 `config/llm/`，常规构建和运行不需要训练框架或下载模型。
这是覆盖语言模型推理流程的微型功能 benchmark；训练于项目内的小语料，不能代表通用大模型质量。
LLM 结果目录额外提供 `llm_report.md`、`llm_summary.json`，报告生成 token、逐层数值误差、
prefill/解码仿真时间和外部访存。中间结果记录的开销计入推理时间，模型初始化单独计时。

## 目录

| 目录 | 用途 |
|---|---|
| `config/` | 所有公开配置、benchmark 预设、版本锁定、配置说明 |
| `scripts/` | 一套 setup/build/run/test/check 入口与唯一仿真拓扑 |
| `integration/` | 仅本设备的原生 C ABI、ELF 装载与异步请求/响应 |
| `benchmarks/` | 按 smoke / memory_roundtrip / tiny_llm 整理的设备源码和使用说明，见[目录索引](benchmarks/README.md) |
| `simulation/` | 独立运行器、NPU → AXI256 适配及来源证据检查 |
| `../axi_StorageStacked/` | 独立公共依赖：AXI 信号端口、AXI2Flit、UCIe、在线内存和共用验收器 |
| `third_party/` | 本项目自己的 CoralNPU 固定版本源码 |
| `.deps/` | 本驱动自己的工具链、编译依赖和缓存 |
| `build/`、`results/` | 编译产物和实际运行证据 |

两个驱动互不链接，均通过公共 AXI256 端口驱动同一份 `axi_StorageStacked` 源码。
默认依赖路径为 `../axi_StorageStacked`；不同目录可用 `export STORAGE_STACK_ROOT=/绝对路径/axi_StorageStacked` 指定。
切换公共项目路径或版本后必须重新 build；运行入口会拒绝路径与构建记录不一致的二进制。
依赖声明在 `config/storage_dependency.json`，每次运行在 `environment.json` 记录公共项目版本/提交号。
内存库分别编译到各驱动的 `build/memsim/`，不会共用另一个驱动的二进制。
本次从本机已准备的固定版本源码与包缓存建立环境，分别在新目录重编译。
源码来源见 `config/sources.json`，当前分拆说明见 `SPLIT_NOTES.md`。
复制到不同路径后执行 setup/build，重建含绝对 RPATH 的产物；不要直接搬用旧编译缓存。
新机器需要 Linux x86-64、Bash、Git、curl、make、patch、tar、C/C++ 基础开发工具与网络，
建议至少 32 GB 内存。默认编译并行度为 12，可在 `config/environment.sh` 修改。
`SS_OFFLINE=1` 只限制 setup 的下载；首次 Bazel 构建还需已经准备好它自己的依赖缓存。

## 验收证据与边界

本次实际执行结果见 [`ACCEPTANCE.md`](ACCEPTANCE.md)。

每次运行保存 `resolved.json`、`environment.json`、`completion.json`、`summary.json`，
并保留 AXI 五通道 VCD、两端 Flit、内存请求/返回、DRAM 命令、DFI、最终内存镜像、
NPU 原生请求/响应日志和分块 HTML。`memsim_view.html` 可按请求查看链路；
在工程目录执行 `python3 -m http.server 8000` 后通过浏览器打开结果页面，交接时复制整个用例目录。

`npu_requests.csv` 记录 RTL 包装器的真实异步请求和响应回调，并与 AXI、Flit、内存数据逐笔核对。
回调接口仍是一个有界事务适配层，不宣称 RTL 管脚与后级信号逐线直连；实际 AXI256 握手以 `axi_wave.vcd` 和五通道日志为准。
只使用独立构建的一套 Accellera SystemC，统一时间单位为 1 fs。没有 gem5 Packet、TLM socket 或主机 CPU。
CoralNPU 执行 RTL 生成模型；每次仿真只启动一个内核。默认 `run` 是外部存储读写/计算自检；
`llm` 执行微型 decoder 的完整前向与贪心生成，采用标量 FP32，RVV 关闭。
内存 PHY/DFI 为行为模型，HBM4 预设含临时时序项；结果用于本模型的功能与时序比较。
当前 NPU 接口支持该 CoreMiniAxi 使用的单拍、非独占 INCR 请求，未知地址或不支持的 burst 直接失败；
外部读写必须经过在线 mem_sim，不存在私有 DDR 回退。多拍 NPU burst、checkpoint、跨设备缓存一致性不在当前支持范围。

长验收允许续跑：`./run.sh test --resume --output results/已有验收目录`。
仅复用配置、来源版本和公共仿真产物大小匹配的已通过场景；更改源码后使用新目录重新验收。
CoralNPU 暂存 ELF 会随预设切换，仅在当前暂存预设与场景相同时比较该 ELF 大小。
同一编译配置的场景并行运行，重新编译设备时使用独占锁，防止运行中替换动态库。

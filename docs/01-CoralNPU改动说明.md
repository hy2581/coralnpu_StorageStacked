# 对 CoralNPU 做了哪些修改

本文对应当前去除 gem5 后的工程。改造的目标是：让 CoralNPU 自己执行程序、发起外部内存读写，并等待真实的链路和内存响应。主要改动集中在仿真包装器、驱动接口、时钟调度和访存适配层。

CoralNPU 的计算端仍然来自本仓库的 RTL。Verilator 把 RTL 转换成可在电脑上运行的 C++ 仿真模型；程序中的指令由这个模型执行。当前实例是 `CoreMiniAxi`，没有启用 RVV 版本。

## 1. 改造后的整体结构

```text
CoralNPU 执行设备程序
        ↓ 发出读写请求
原生异步接口：记录请求，等待稍后返回
        ↓
AXI256 适配器：整理地址、数据、字节掩码，执行五通道握手
        ↓
AXI2Flit：把总线请求装入链路数据帧
        ↓
UCIe：传输请求和响应，处理链路反压与错误重放
        ↓
在线 mem_sim：执行内存访问，给出读数据或写完成
        ↑ 响应沿原路径返回 CoralNPU
```

整个过程由一套独立的 SystemC 调度。可以把它理解为所有模块共用的仿真时钟管理器：NPU、AXI 和内存可以有不同周期，但它们使用同一条时间轴，最小时间单位为 1 fs。

## 2. 让访存支持“先请求，后返回”

外部内存需要经过总线、链路和控制器，不会在请求发出的同一时刻立即完成。因此包装器提供了异步回调：先把请求交出去，等内存完成后，再把读数据或写响应送回 RTL。

主要代码在 [core_mini_axi_wrapper.h](../third_party/coralnpu/hw_sim/core_mini_axi_wrapper.h) 和 [hw_primitives.h](../third_party/coralnpu/hw_sim/hw_primitives.h)。

| 接口 | 通俗解释 |
|---|---|
| `RegisterAsyncReadCallback` | 告诉包装器：读请求出现后交给谁处理 |
| `RegisterAsyncWriteCallback` | 告诉包装器：写地址和数据交给谁处理 |
| `CompleteRead` / `CompleteWrite` | 外部访问完成后，把结果送回 NPU |
| `RegisterRequestReady` | 下游处理不过来时，让 NPU 暂停提交新请求 |
| `halted()` / `wfi()` | 查看程序是否停止或进入等待状态 |

“反压”就是下游暂时没有空位时通知上游等待。请求不能无限堆积，也不能在队列满时被丢弃。项目通过这个机制把内存和链路的拥堵传回 NPU。

包装器还提供非阻塞启动写入接口 `EnqueueWriteWord`。运行器可以分步写入启动寄存器，由统一调度继续推进设备，状态查询和响应注入本身不会偷偷推进 NPU 时钟。

## 3. 增加独立的 CoralNPU 驱动接口

本工程的驱动代码在 [integration/coralnpu/coralnpu_native.cc](../integration/coralnpu/coralnpu_native.cc)，对外接口见 [coralnpu_native.h](../integration/coralnpu/coralnpu_native.h)。这里的“驱动”是仿真程序与 RTL 模型之间的接口，不是操作系统中的内核驱动。

它负责创建设备、复位、装载程序、启动、按时钟步进、提交访存请求、接收响应，以及读取完成状态。对外使用 C ABI，即一组能被主程序稳定调用的 C 风格函数；SystemC 对象不跨过这个接口。

启动时把 ELF 装入 NPU 的本地 TCM。ELF 是编译后的设备程序文件，TCM 是核旁边的本地存储。驱动只允许这种启动装载落在本地 TCM；外部内存的数据由设备程序运行后经过完整链路写入。

每个外部请求带有序号，驱动会核对响应是否对应尚未完成的请求。未知地址、不支持的访问形式，以及重复或对不上号的响应会使运行失败。

## 4. 把 NPU 的 128 位接口接到 AXI256

适配代码在 [simulation/axi_master.cc](../simulation/axi_master.cc)。CoralNPU 原生一拍带 16 字节，后级 AXI256 一拍有 32 字节，适配器根据地址把数据放入正确的字节位置。

例如，设备程序写一个 32 位整数，只需要修改 4 字节。接口仍携带 16 字节数据，但字节掩码只选中其中 4 字节；适配到 32 字节总线后，这个选择关系继续保留。读响应也从正确的字节位置提取，再送回 NPU。

适配器还管理有限数量的未完成请求、链路使用的事务 ID，以及同原生 ID、同读写方向的响应顺序。AXI 的 AW、W、B、AR、R 五通道分别处理写地址、写数据、写响应、读地址和读数据。

这些是实际的后级 AXI 握手。NPU 与适配器之间使用有界事务回调，因此应把它理解为接口适配，而不是 NPU 管脚与 AXI256 管脚逐线直连。

## 5. 去掉 gem5，统一构建与调度

当前工程移除了原有 gem5 设备桥、Packet/TLM 接入、旧运行脚本和 HETTrace 投影路径。现在由 [simulation/main.cc](../simulation/main.cc) 创建仿真拓扑，并调用原生 CoralNPU 驱动。

| 产物 | 用途 |
|---|---|
| `build/native/coralnpu_sim` | 主仿真程序，统一调度计算端、链路和在线内存 |
| `build/coralnpu/libcoralnpu-native.so` | CoralNPU RTL 模型及原生驱动库 |
| `build/coralnpu/memory_roundtrip.elf` | NPU 真正执行的设备程序 |
| `build/memsim/libstoragestacked_memsim.so` | 在线内存模型库 |

[CMakeLists.txt](../CMakeLists.txt) 在本工程构建独立 Accellera SystemC 并链接主程序。设备库走原生 C++ Verilator 构建路径；[构建脚本](../scripts/build_device.sh) 使用 `storagestacked_native_cpp=1`，[对应补丁](../third_party/coralnpu/third_party/rules_hdl/0018-Optional-SystemC-for-native-Cpp.patch) 避免把另一套 SystemC 带进设备库。

以后修改驱动，应编辑 `integration/coralnpu/`。其中的文件由 [install.sh](../integration/coralnpu/install.sh) 复制到 `third_party/coralnpu/native/` 供 Bazel 构建；直接修改复制品可能在下次构建时被覆盖。

## 6. 怎样证明改造后的链路有效

验收同时检查 NPU 计算结果和各层实际数据。`npu_requests.csv` 记录设备请求与回调；`axi_wave.vcd` 记录五通道波形；Flit、内存请求/返回、DRAM/DFI 和最终内存镜像用于继续向下核对。

2026-09-24 的[独立架构验收](../ACCEPTANCE.md)已通过默认、慢内存、CRC 重放、跨步和反压五个场景。默认内核完成 256 笔外部读写；内存时间倍率从 1 改成 4 后，NPU 从 8943 周期增至 13223 周期。数据量相同、等待时间变长，说明内存返回时序确实影响设备执行。

当前 NPU 原生接口支持单拍、非独占 INCR 请求；上述内存负载实测最多只有一笔在途访问。在此基础上，项目现已增加微型字符级 Transformer 的完整推理验收，见第三篇说明；通用大模型质量、多在途吞吐和物理层合规性仍不属于这些用例的证明范围。

配置和运行方法见 [参数配置与 Benchmark 使用](02-参数配置与Benchmark使用.md)；当前负载具体计算什么，见 [当前 LLM 负载与执行位置](03-当前LLM负载与执行位置.md)。

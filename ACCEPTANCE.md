# 三项目拆分验收（2026-09-25）

实际执行：`./run.sh build` 与 `./run.sh test --output results/split-acceptance`。
独立性检查 `./run.sh check` 通过；它只是环境检查，计算和链路结论来自后面的实际运行。

- 原生内存测试 19/19 通过，在线 C ABI 检查通过。
- 13 个实际设备场景全部 `passed: true`。
- 每个场景保留计算结果、来源请求、AXI 五通道波形、原始 Flit、在线内存、DRAM/DFI 与最终镜像核对。
- 相同默认负载把内存周期放慢 4 倍后，设备周期从 8,943 增至 13,223，证明存储响应继续反馈到设备执行。

CoralNPU 还覆盖两个 SMOKE、五个内存读写场景和六个 LLM 场景，包括 KV cache 关闭、长上下文、反压、重放和错误证据拒绝。
完整 build 后再次执行 SMOKE 通过，验证 Bazel 后台进程不再继承构建锁。

当前配置和摘要位于 [validation/2026-09-25-three-projects](validation/2026-09-25-three-projects/summary.json)。
完整原始波形与日志在本机 `results/split-acceptance/`，未把历史旧布局结果当作此次拆分结果。

## 拆分边界

本仓库没有 `axi2flit/`、`ucie-model/`、`protocol/`、`mem_sim/` 或独立 SystemC 副本。
编译入口只从 `STORAGE_STACK_ROOT` 指定的公共存储项目取得相应源码；不使用 submodule。
存储库独立编译到本驱动 `build/memsim/`，不加载另一个处理器驱动的二进制。
切换公共项目路径/版本后未重建的运行会被拒绝，已实际检查这条拒绝路径。
公共源码的基线版本和此次验收使用的代码版本见 `config/storage_dependency.json`；
各场景 `environment.json` 保留当时的公共项目提交与工作树状态。后续提交仅整理文档/验收资料。

## 复现

```bash
export STORAGE_STACK_ROOT=/绝对路径/axi_StorageStacked
./run.sh setup
./run.sh build
./run.sh test --output results/new-acceptance
```

总摘要及各场景均须 `passed: true`。实际 AXI 数据宽度为 256 bit，时基为 1 fs。
模型能力和驱动各自的限制见 README.md；PHY/DFI 为行为模型，验收不是物理硬件签核。

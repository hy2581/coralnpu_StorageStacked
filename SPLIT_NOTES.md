# 三项目拆分（2026-09-25）

本仓库现在是 CoralNPU RTL 驱动。
保留计算程序、原生适配、AXI master、配置和来源验收；AXI slave 之后的源码仅存在于独立的 `axi_StorageStacked`。

公共组件：`storage_axi`、`axi2flit`、`ucie-model`、`protocol`、`mem_sim`、独立 SystemC、共用波形/Flit/内存验收器。
依赖由 `config/storage_dependency.json` 声明，用 `STORAGE_STACK_ROOT` 选择路径；不自动复制源码，不使用 submodule。
运行接口、benchmark 和全部原始验收门槛保留。两驱动各自构建内存库与适配代码，各有自己的内存状态。

旧源码、未提交改动和 Git 历史在本机工作区外备份；GitHub 发布只在新链路验收通过后替换当前两个仓库。
历史 `validation/2026-09-24*` 和此前 2026-09-25 目录记录旧布局，不作为本次拆分验收。
本次结果见 `ACCEPTANCE.md`。

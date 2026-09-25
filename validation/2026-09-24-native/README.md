# 独立 CoralNPU / SystemC 验收（2026-09-24）

这些摘要来自新的完整验收，没有复用迁移前结果。

```bash
./run.sh build
./run.sh test --output results/acceptance-native-20260924-2255
./run.sh check
./run.sh run --output results/native-default-final-20260924
```

完整 AXI 五通道 VCD、Flit、内存请求与返回、DRAM/DFI、内存镜像、
NPU 原生请求/响应和 HTML 保留在上述 results 目录。这里保存配置、
来源版本、产物文件名与大小，以及各层检查摘要。
当前设备 ELF 已切回默认 64 字配置；跨步场景的环境快照记录其当时的 ELF。

验收结论、测量范围与限制见 [ACCEPTANCE.md](../../ACCEPTANCE.md)。

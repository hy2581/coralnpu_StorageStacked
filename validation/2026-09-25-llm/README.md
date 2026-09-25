# 微型语言模型部署验收（2026-09-25）

实际命令：`./run.sh test --output results/acceptance-llm-v1`。
[summary.json](summary.json) 为完整汇总，11 个场景均新跑且通过，未复用旧结果。

本目录保留小型记录，原始结果没有移动或删除：

- `llm*/`：六个 LLM 场景的计算、计时、访存、协议与波形检查汇总。
- `default/`、`slow/`、`replay/`、`backpressure/`、`alternate/`：五个原有内存场景。
- 各例 `resolved.json`：当次架构、benchmark 和完整模型权重快照。
- 各例 `environment.json`：来源版本与实际运行文件名、大小。
- `native-tests.log`、`api/`：19 项原生测试与在线 C ABI 检查。
- `negative.json`、`llm-negative.json`：损坏证据、非法配置与边界检查。
- `llm-training.log`、`llm-kernel-build.log`：本地模型训练和首次设备 ELF 构建记录。
- `deployment-files.json`：来源基线提交、模型版本、部署源码文件名与大小；包含本地未提交修改。
- `raw-evidence-files.json`：原始证据位置、主要文件名与大小、各例完整目录体积。

完整原始记录保存在项目内 `results/acceptance-llm-v1/`，包括 `axi_wave.vcd`、
`npu_requests.csv`、`ucie_soc.csv`、`ucie_mem.csv`、`ucie_flits.csv`、内存/DRAM/DFI、
最终镜像以及 HTML 和配套数据目录。交接完整链路证据时复制整个原始用例目录；
本目录的检查汇总不能代替原始波形。

完整解释见 [ACCEPTANCE.md](../../ACCEPTANCE.md) 和 [LLM 中文说明](../../docs/03-当前LLM负载与执行位置.md)。

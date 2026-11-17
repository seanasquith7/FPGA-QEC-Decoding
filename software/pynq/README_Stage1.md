# Stage 1 — Test Data Generation

本阶段把团队的 Stim 仿真电路变成可重复使用的测试数据集。只生成数据和真实逻辑标签，不实现软件解码器、FPGA 通信或解码性能统计。

## 已检查的仓库

2026-11-17 通过只读 GitHub 连接检查 `seanasquith7/FPGA-QEC-Decoding` 的 `main`。仓库包含：

- `simulation/stim-circuit.py`：构建 `surface_code:rotated_memory_z` 电路；distance=3、rounds=3、四个噪声通道概率均为 0.01；定义 NUM_SHOTS=100000，但尚未采样。
- `simulation/.vscode/settings.json`：VS Code 的 Conda 环境偏好。

所见 main 中没有数据导出、软件解码器、RTL 或硬件输入协议文件。仓库状态不能代表组员尚未上传的工作。

Stage 1 最初在仓库外开发，随后已整理到实际克隆的 Git 仓库中。生成器现在直接使用本仓库的 `simulation/stim-circuit.py`，没有修改团队原文件。最初检查的参考 blob SHA 为 `176ad40748b943701cd99c5816ebf470e49be8f2`。没有 commit、push 或远程修改。

## 新增文件

| 文件 | 用途 |
| --- | --- |
| `software/pynq/generate_test_data.py` | 命令行生成器，默认载入团队现有电路 |
| `software/pynq/test_generate_test_data.py` | 使用 unittest 验证数据与标签、文件读写及命令行 |
| `software/pynq/requirements.txt` | 固定 Stim 和 NumPy 版本 |
| `software/pynq/README_Stage1.md` | 操作说明与数据格式 |
| `.gitignore` | 忽略虚拟环境、缓存和生成数据 |

## 数据代表什么

一次 shot 是完整执行一次带噪声的纠错电路，包含所有 rounds。

`detectors.01` 中 1 表示对应 detector 的测量奇偶关系偏离无噪声参考。这里 syndrome 指 detector events，而不是原始测量 bit，也不是物理错误的位置。

`observables.01` 中 1 表示对应逻辑 observable 相对无噪声参考发生翻转。这是后续解码器需要预测的真实标签。它不能单独用于报告解码错误率；后续需要检查预测值和真实值是否不同。逻辑标签保留给主机验证，不能作为解码器输入。

生成器在一次 `sampler.sample(shots=..., separate_observables=True)` 调用中取得两个数组，保证第 i 行对应同一次实验。默认复用原脚本的 `circuit` 和四个参数常量；用 `runpy` 加载时会执行该受信任的本地脚本，当前仅有版本打印和电路构建，版本打印会被隐藏。

`--distance`、`--rounds`、`--noise` 改变时，会显式重新生成与当前团队脚本相同类型、相同四类噪声的电路。如果团队以后换了电路类型或噪声模型，应重新检查这些覆盖选项；不传覆盖选项时仍直接使用团队的 `circuit`。参数 `noise=0.01` 表示每个通道的配置概率，不是整次 shot 的错误概率。

## 安装与运行（Windows PowerShell）

在项目根目录执行。新电脑需要先安装 Python 3.11 或更高版本（测试环境为 Python 3.12）。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --progress-bar off -r software\pynq\requirements.txt
```

仓库根目录已创建 `.venv` 时可以跳过第一行。无需激活虚拟环境。当前本机此前安装好的环境保留在仓库的上一层，因此也可以直接从仓库根目录运行以下命令：

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s software/pynq -v
..\.venv\Scripts\python.exe software\pynq\generate_test_data.py --shots 1000 --seed 42 --output software\pynq\output\my_test_1000
```

后续示例使用新安装在仓库根目录的 `.venv`；若继续使用上一层环境，把 `.\.venv` 替换为 `..\.venv`。

先生成 1000 次带噪声实验：

```powershell
.\.venv\Scripts\python.exe software\pynq\generate_test_data.py --shots 1000 --seed 42 --output software\pynq\output\smoke_1000
```

生成原脚本默认的 100000 次实验：

```powershell
.\.venv\Scripts\python.exe software\pynq\generate_test_data.py --seed 42 --output software\pynq\output\default_100000
```

生成无噪声对照：

```powershell
.\.venv\Scripts\python.exe software\pynq\generate_test_data.py --shots 100 --noise 0 --seed 42 --output software\pynq\output\noiseless_100
```

输出目录必须不存在。重复运行时换一个目录名；程序会拒绝覆盖已有目录。写入失败时目录可能不完整，请选择新目录重试。`metadata.json` 最后写入，表示导出已完成；其中的哈希可用于检查文件是否被改动。

## 输出格式

| 文件 | 内容 |
| --- | --- |
| `detectors.01` | 每行一个 shot；从左到右 D0、D1……；仅 ASCII 0/1，无标题或分隔符，LF 换行 |
| `observables.01` | 与 detector 文件逐行对应；从左到右 L0、L1……；相同 01 格式 |
| `circuit.stim` | 实际被采样的完整电路 |
| `detector_error_model.dem` | 从同一电路导出的、分解后的 detector error model，供团队后续解码工作使用 |
| `metadata.json` | 参数、维度、seed、来源哈希、软件版本、格式约定、数据计数及输出文件 SHA-256 |

默认电路每个 shot 有 24 个 detector bits 和 1 个 observable bit。因此 1000 shots 的两个数据文件分别有 1000 行，每行 24/1 个 bit。24 个 detector 是整个多轮电路的输出，不能当作每轮都有 24 位。

这是离线测试文件约定。ASCII bit 的左右顺序不决定未来字节打包的位序，也没有指定 UART、USB、以太网、帧头或握手方式。任何硬件位序映射都留待 Stage 2 协商。

当前版本一次将所有 shots 放入内存，适合默认的 100000 shots。更大的 distance、rounds 或 shots 会增加内存开销。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s software/pynq -v
```

测试包括：

1. 原电路不变；保存后用 Stim 官方读取函数读回，与同 seed 的一次直接采样逐位比较，确认数据和标签配对。
2. noise=0 时，detector events 和逻辑翻转均全零。
3. 人为构造确定的逻辑翻转，确认 detector=0 而真实逻辑标签=1 能被保存。
4. 当前同一运行环境、同 seed 重复采样结果一致。
5. 参数覆盖及单个 shot 能正常导出。
6. 非法参数不会创建输出目录。
7. 已有输出目录不会被覆盖。
8. 命令行成功导出、重复路径报错。

测试通过说明 Stage 1 数据生成和保存功能正常，不代表 FPGA 解码正确。相同 seed 的逐位复现依赖同一 Stim 版本、机器指令能力和相同采样调用序列；跨机器或跨版本不保证一致。应将已生成的文件视为之后硬件验证的固定输入。

## 本次实测结果（2026-11-17）

Python 3.12、Stim 1.15.0、NumPy 2.3.5；8 个自动测试全部通过。以下数据集已在本地生成，seed 均为 42：

| 输出目录（位于 software/pynq/output） | shots | 每 shot detector/observable 数 | 出现 detector events 的 shots | 出现逻辑翻转的 shots |
| --- | --- | --- | --- | --- |
| `smoke_1000` | 1000 | 24 / 1 | 817 | 179 |
| `noiseless_100` | 100 | 24 / 1 | 0 | 0 |
| `default_100000` | 100000 | 24 / 1 | 81719 | 18567 |

逻辑翻转计数不是解码失败计数。本阶段没有计算解码准确率。

安装期间 pip 的在线下载进度发生停滞，因此本次从官方 PyPI 下载了对应 wheel，核对 PyPI 发布的 SHA-256 后离线安装。依赖已就绪；本机从实际仓库根目录可直接使用 `..\.venv\Scripts\python.exe` 运行。Stage 1 文件及此前生成的数据已移入仓库，虚拟环境留在上一层。迁移后应再次运行上面的测试命令确认与仓库内原始电路兼容。

参考：

- https://github.com/seanasquith7/FPGA-QEC-Decoding/blob/main/simulation/stim-circuit.py
- https://github.com/quantumlib/Stim/wiki/Stim-v1.15-Python-API-Reference
- https://github.com/quantumlib/Stim/blob/main/doc/file_format_01.md

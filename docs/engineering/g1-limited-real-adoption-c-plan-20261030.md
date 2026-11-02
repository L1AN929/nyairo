# G1第三批c：开启候选观测的一次限定运行

> 注：本文记录的内部网关地址已脱敏为 `gw.example.invalid`；历史观察、失败与结论保持原样，不表示当时实际使用占位地址。

2026-10-30本人在本窗口对第三批整块范围回应「不需要问我，授权那个窗口到凌晨一点」。据上下文执行此前推荐的必要身份适配、零模型准备及唯一真实运行；指定监护窗口 local_34510d4b-4e13-40e6-a17f-6e6636f3b128 的同边界转文授权延至2026-10-31 01:00 CST，不逐项请示。新生产规则/安全扩权/旧实例/删除/外发/git发布等例外不放开。

固定c root `/root/nyairo-g1-real-20261030-c`，authority `/root/nyairo-g1-real-20261030-c-memory-authority`，run `run:g1-real-20261030-c`，域 `g1:real:20261030-c`，历史保守121/300。新root及authority若已存在或被占用即停，不换名追成功。新增预留硬cap20，Decision/内容/失败共用且跨PID累计，最坏141/300；失败不退，未用余额不续用。总窗口最多1800真实秒含停启，另受2026-10-31 01:00更早截止。原a/b映射与17:00历史截止不变，不重开或读写。

沿现有G1同A链，真实模型自主选择作品和内容，首合法成果定义A；operator中断/两PID有序停启与同A合法提交的丢答/原operation对账、并发CAS各一次仍明确标注。没选回原A或没触发后半链就NOT_COVERED，不操纵静默/造资格/强制CONTINUE或NO_ACTION。不得把别作续写算同A成功。

本次显式 `--capture-candidates`，实验默认仍关闭；材料化/合法请求/同次最终结构候选及POST_TICK_READ旁证分开。只存已知ID/固定枚举/计数/布尔，不prompt/正文/异常原文。事后资格不是选择前原子快照；原始discover/缺接缝UNKNOWN，记录不保证完整因果。生产源码/候选排序/资格/模型输入不改。

参数沿用：custom、https://gw.example.invalid/v1、cn:deepseek-v4.1-flash；温度0、非stream、Decision4096/内容2048，无seed/top_p；固定1分钟check，真实业务时钟，World默认gate不变。密钥只在唯一真实run同shell source /root/.nyairo-model.env，绝不读取/回显/写仓库。provider/transport失败、坏账/错绑定/安全拒绝整批停，不诊断探测或重复run。

父级仅执行新 `tests.test_g1_batch_c`（HTTP硬拒/真owner与替身transport/scratch保留），核c身份与profile截止跨phase、cap共享、trace传播、a/b不漂移和错身份拒绝。通过后重核新路径/并发占额，首次prepare→audit prepared/startnull/ledger0→唯一run。运行期间源码冻结无writer。结束仅c安全元数据/trace/audit与自有PID，不读取旧root；自然选择、operator干预、覆盖/未覆盖、预留与可证发送分开。无提交推送部署。

## 实现与命令（施工后追加；不改变上面的授权范围）

- 身份：`BATCH_PROFILES["c"]` 固定 root `/root/nyairo-g1-real-20261030-c`、独立 authority
  `/root/nyairo-g1-real-20261030-c-memory-authority`、run `run:g1-real-20261030-c`、
  域 `g1:real:20261030-c`、历史基线 121。CLI 仍是封闭的 `--batch a|b|c`，没有任意
  root/run/预算/截止参数，环境变量也没有入口。
- 截止：`BatchProfile.deadline` 是唯一运行期来源。a/b 保持 `2026-10-30T17:00:00+08:00`，
  c 是 `2026-10-31T01:00:00+08:00`；模块级 `DEADLINE` 常量仍等于 a。窗口
  `deadline_wall`/`deadline_basis`、`prereg.deadline`、开跑前检查、phase config 与
  `audit.deadline` 全部取所选批次的同一个值；prereg/config 缺截止或写成别的批次，
  一律 `BATCH_IDENTITY_MISMATCH`。
- 观测：`--capture-candidates` 仍默认关闭，只有 c 的真实 run 显式加；p1/p2 从 config
  传播该开关，trace 只写在所选 root 的 `experiment-g1/` 下。
- 命令（父级执行；worker 未跑真实 prepare/run）：
  ```bash
  PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v tests.test_g1_batch_c
  python -B scripts/g1_real_adoption.py prepare --root /root/nyairo-g1-real-20261030-c --batch c
  python -B scripts/g1_real_adoption.py audit   --root /root/nyairo-g1-real-20261030-c --batch c
  python -B scripts/g1_real_adoption.py run     --root /root/nyairo-g1-real-20261030-c --batch c \
      --execute-approved --capture-candidates
  ```
- 实现边界：只改 `scripts/g1_real_adoption.py`、新增 `tests/test_g1_batch_c.py`，并在
  `tests/test_g1_real_adoption.py` 里把封闭集合断言从 `{a, b}` 改成 `{a, b, c}`；
  不碰 `components/`、旧批结果、生产候选排序或默认 gate。

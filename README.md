# deepseek-reasoning-experiment

用 DeepSeek 官方 API 复现 DeepSeek Harness（DSH）的调用方式，验证：

1. 多轮对话中，哪些历史 reasoning 会被忽略；
2. 同一条用户消息内的多步工具调用（agent loop）中，reasoning 是否必须回传；
3. 被忽略的 reasoning 是否计费；
4. 实测行为与官方文档是否一致；
5. DSH 实际线上请求是否如实带回历史 reasoning（源码审计 + 代理抓包）。

> **API key 状态**：`code/config.py` 中的 key 已在实验结束后删除/吊销，不能使用；保留仅为复现代码形状。详见 [SECURITY.md](SECURITY.md)。
>
> 入口：[RESULTS.md](RESULTS.md)（数据与结果总览）· [DATA.md](DATA.md)（数据清单）· [REPORT.md](REPORT.md)（完整报告）· [dsh_verify/SOURCE_AUDIT.md](dsh_verify/SOURCE_AUDIT.md)（DSH 发送路径）。
>
> 彩蛋：[session/README.md](session/README.md) 末尾，写给打开仓库的你。

## 两种接口

| 名称 | 端点 | 对应 |
|---|---|---|
| anthropic | `POST https://api.deepseek.com/anthropic/v1/messages` | DSH 的 `dsh-llm-deepseek` 实际使用的接口（thinking block + signature） |
| openai | `POST https://api.deepseek.com/chat/completions` | DeepSeek 官方《Thinking Mode》文档描述的接口（reasoning_content） |

Anthropic 客户端按 DSH 的线上格式构造：`x-api-key`、`anthropic-version: 2023-06-01`、
SSE 流式响应、`thinking: {type: enabled}`、`output_config: {effort}`，
并在历史 assistant 消息中回传 `thinking` 文本与 `signature`。

## 目录结构

```text
deepseek-reasoning-experiment/
├── README.md / REPORT.md / RESULTS.md / DATA.md / SECURITY.md
├── requirements.txt / .gitignore
├── code/                          Python 代码（配置、客户端、探针、实验、分析）
│   ├── config.py                  key/模型/端点/公共参数
│   ├── ds_client.py               两个接口的最小客户端
│   ├── probes.py                  共享探针、计费、记录工具
│   ├── exp1..exp9_intent.py       实验 1–9
│   ├── exp9_parallel.py           实验 9 并行版（1000 次/设置）
│   ├── analyze_results.py         主实验汇总
│   ├── analyze_exp9_parallel.py   并行数据重分类/统计
│   ├── run_all.py                 主实验一键运行
│   ├── show_exp9_transcripts.py   导出实验 9 对话样例
│   └── smoke_test.py              最小连通性检查
├── results/                       主实验 JSON + 汇总 + 实验9并行原始数据
│   └── exp9_parallel/             10 个 JSONL（10,000 条，约 26MB）+ 概率矩阵 + 统计
├── dsh_verify/                    隔离 DSH 实例 + API 代理 + 源码审计 + 抓包验证
└── session/                       会话导出存放目录（含彩蛋）
```

## 快速开始

```bash
cd deepseek-reasoning-experiment
pip install -r requirements.txt

# API key：code/config.py 中的 key 已删除，请使用自己的 key
export DEEPSEEK_API_KEY=sk-...

python3 code/smoke_test.py     # 先确认两种接口都能通
python3 code/run_all.py        # 跑全部主实验并生成 results/SUMMARY.md
```

单独跑某个实验（参数为接口名 `anthropic` 或 `openai`）：

```bash
python3 code/exp1_no_tools_multiturn.py anthropic
python3 code/exp2_tools_multistep.py openai
python3 code/exp3_billing.py anthropic
python3 code/exp4_visibility.py openai
python3 code/exp5_multiturn_tool_reasoning.py anthropic
python3 code/analyze_results.py
```

## 并行复测（实验 9）

用线程池并发跑实验 9，每个设置 × 每个接口默认 1000 次，逐条保存完整原始对话，并生成概率矩阵：

```bash
python3 code/exp9_parallel.py --trials 1000 --concurrency 300 --surface both
python3 code/analyze_exp9_parallel.py
```

产出：

- `results/exp9_parallel/{surface}_{setting}.jsonl`：每行一条完整对话（请求 messages + 响应 thinking/text/usage/stop_reason）；
- `results/exp9_parallel/probability_matrix_refined.{md,csv,json}`：重分类后的概率矩阵；
- `results/exp9_parallel/statistics.md`：Wilson 置信区间、与基线的两比例检验、意图提及率；
- `results/exp9_parallel/probability_matrix.*`：初版分类矩阵（未剔除 no_answer/tool_call）。

本次实际运行：10,000 次请求，并发 300，174 秒，0 失败，估算费用约 $1.06（off-peak）。

## DSH 发送路径验证

```bash
cd dsh_verify
python3 proxy.py    # 127.0.0.1:18080，落盘请求后转发到真实 API
# 另开终端按 dsh_verify/README.md 用隔离 DSH_HOME 跑 headless/web
python3 verify_capture.py
```

结果见 `dsh_verify/SOURCE_AUDIT.md` 与 `dsh_verify/VERIFICATION.md`。测试用的代理与 3099 端口实例已关闭。

## 方法要点

- **canary**：让模型把随机 8 位十六进制串只写进 reasoning（不出现在任何 user 文本里），
  后续轮次要求复述。只有 reasoning 真的进入上下文，模型才可能答对。
- **token 记账**：在 system 相同的配对请求中，只增加一个 13,500 字符（约 3,001 token）的
  synthetic reasoning 块，比较 `usage` 中的输入 token 总量。增量 0 表示被丢弃；
  增量约 3,001 表示被完整计入并按输入 token 计费。
- **冷/热历史**：用随机 system 前缀模拟会话恢复；用服务端从未见过的 tool_call id
  模拟服务端无法恢复 reasoning 的情形，从而复现 400。

## 说明与安全

- 费用按官方价格与 API 返回的 usage 估算，不是账单明细；余额变化不能单独归因于本实验。
- `code/config.py` 中的 key 已删除/吊销，不能使用；复现请改用环境变量并轮换自己的 key。
- 仅测试 `deepseek-flash`、`reasoning_effort=high`；并行复测为 off-peak。

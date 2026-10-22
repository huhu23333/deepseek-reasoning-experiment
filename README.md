# deepseek-reasoning-experiment

用 DeepSeek 官方 API 复现 DeepSeek Harness（DSH）的调用方式，验证：

1. 多轮对话中，哪些历史 reasoning 会被忽略；
2. 同一条用户消息内的多步工具调用（agent loop）中，reasoning 是否必须回传；
3. 被忽略的 reasoning 是否计费；
4. 实测行为与官方文档是否一致。

> **API key 状态**：仓库里 `config.py` 中的 key 已在实验结束后删除/吊销，不能使用；保留仅为复现代码形状。详见 [SECURITY.md](SECURITY.md)。
>
> 入口：数据与结果总览 [RESULTS.md](RESULTS.md)；数据文件清单 [DATA.md](DATA.md)；完整结论 [REPORT.md](REPORT.md)；DSH 源码/抓包验证 [dsh_verify/SOURCE_AUDIT.md](dsh_verify/SOURCE_AUDIT.md)。

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
config.py                      key、模型、端点、公共参数
ds_client.py                   两个接口的最小客户端（requests + 手写 SSE 解析）
probes.py                      中性 Surface 抽象、canary、token 记账、定价、结果记录
exp1_no_tools_multiturn.py     实验1：无 tools 的多轮对话
exp2_tools_multistep.py        实验2：有 tools 的同轮多步工具循环 + 400 规则 + signature
exp3_billing.py                实验3：计费（忽略/包含/输出侧/缓存）
exp4_visibility.py             实验4：行为侧可见性（重复试验）
exp5_multiturn_tool_reasoning.py 实验5：多轮后续轮次中前几轮 tool reasoning 的沿用与计费
exp6_completed_answer_reasoning.py 实验6：模型完成回答后，下一轮是否仍带上该轮 reasoning
exp7_thinking_gate.py          实验7：thinking 开关对历史 reasoning 采用的影响
exp8_reasoning_tamper.py       实验8：手动篡改历史 reasoning，验证其对下一轮回答的影响
exp9_intent_injection.py       实验9：仅在 reasoning 中注入意图，验证新开放对话是否体现
exp9_parallel.py               实验9并行版：线程池跑 1000 次/设置，逐条保存原始 JSONL
analyze_exp9_parallel.py       并行结果离线重分类、概率矩阵与统计
analyze_results.py             汇总 results/*.json，生成 SUMMARY.md / summary.json
run_all.py                     一键跑全部实验并汇总
smoke_test.py                  最小连通性检查
results/                       实验原始 JSON、SUMMARY.md、summary.json
results/exp9_parallel/         实验9并行版：10 个原始 JSONL（约 26MB）+ 概率矩阵 + 统计
dsh_verify/                    隔离 DSH 实例 + API 代理 + 源码审计 + 抓包验证
```

## 快速开始

```bash
pip install -r requirements.txt

# API key：config.py 已内置本次实验所用的 key，也可用环境变量覆盖
export DEEPSEEK_API_KEY=sk-...

python3 smoke_test.py     # 先确认两种接口都能通
python3 run_all.py        # 跑全部实验并生成 results/SUMMARY.md
```

单独跑某个实验（参数为接口名 `anthropic` 或 `openai`）：

```bash
python3 exp1_no_tools_multiturn.py anthropic
python3 exp2_tools_multistep.py openai
python3 exp3_billing.py anthropic
python3 exp4_visibility.py openai
python3 exp5_multiturn_tool_reasoning.py anthropic
python3 analyze_results.py
```


## 并行复测（实验 9）

用线程池并发跑实验 9，每个设置 × 每个接口默认 1000 次，逐条保存完整原始对话，并生成概率矩阵：

```bash
python3 exp9_parallel.py --trials 1000 --concurrency 300 --surface both
python3 analyze_exp9_parallel.py
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

结果见 `dsh_verify/SOURCE_AUDIT.md` 与 `dsh_verify/VERIFICATION.md`。

## 方法要点

- **canary**：让模型把随机 8 位十六进制串只写进 reasoning（不出现在任何 user 文本里），
  后续轮次要求复述。只有 reasoning 真的进入上下文，模型才可能答对。
- **token 记账**：在 system 相同的配对请求中，只增加一个 13,500 字符（约 3,001 token）的
  synthetic reasoning 块，比较 `usage` 中的输入 token 总量。增量 0 表示被丢弃；
  增量约 3,001 表示被完整计入并按输入 token 计费。
- **冷/热历史**：用随机 system 前缀模拟会话恢复；用服务端从未见过的 tool_call id
  模拟服务端无法恢复 reasoning 的情形，从而复现 400。

## 说明与安全

- 费用按官方价格与 API 返回的 usage 估算，不是账单明细。本次实验期间同一 key
  也被当前 DSH 会话使用，因此余额变化不能单独归因于本实验。
- `config.py` 中的 key 是本次实验提供的 key；若要长期使用请改为环境变量并轮换该 key。
- 仅测试 `deepseek-flash`、`reasoning_effort=high`、高峰时段。

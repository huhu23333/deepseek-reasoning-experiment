# 实验结果总览（数据 + 结论）

> **API key 已删除/吊销**：`config.py` 中的 key 不可用，详见 [SECURITY.md](SECURITY.md)。

## 0. 环境与总量

- 模型：`deepseek-flash`，`reasoning_effort=high`；两种线上接口：Anthropic Messages（DSH 实际使用）与 OpenAI Chat Completions（官方文档格式）。
- 运行时间：2026-10-22 16:12–20:05（Asia/Shanghai）。主实验为 peak 计费，实验 9 的 1000 次并行复测为 off-peak。
- 价格：deepseek-flash peak 输入缓存命中 $0.006/M、未命中 $0.30/M、输出 $1.20/M；off-peak 减半。费用均为 usage × 官方价估算，不是账单明细。

| 批次 | 请求数 | 输入 tokens | 输出 tokens | 估算费用 | 说明 |
|---|---|---|---|---|---|
| 主实验套件（实验 1–9） | 272 | 190,754 | 36,339 | **$0.0574** | 含 2 个预期 400 错误 |
| 实验 9 并行复测 | 10,000 | — | — | **$1.0643** | 5 设置 × 2 接口 × 1000；0 失败；174.2 秒 |
| DSH 隔离实例代理验证 | 6 | — | — | 未单独统计 | 2 个主请求会话 + 2 个 session-title 请求 |

原始数据与统计文件见 [DATA.md](DATA.md)；完整报告见 [REPORT.md](REPORT.md)。

## 1. 无 tools 的多轮对话（实验 1）

| 用例 | anthropic | openai |
|---|---|---|
| 第 2 轮回传 reasoning | UNKNOWN，输入 143 | UNKNOWN，输入 141 |
| 第 2 轮去掉 reasoning | UNKNOWN，输入 146 | UNKNOWN，输入 144 |
| 第 3 轮回传全部 reasoning | UNKNOWN，输入 177 | UNKNOWN，输入 185 |
| 13,500 字符 reasoning 块输入增量 | **0** | **0** |

结论：无 tools 时历史 reasoning 不进入上下文、不进入缓存、不计费；文档未提计费，实测为 0。

## 2. 有 tools 的同轮多步工具调用（实验 2）

| 用例 | anthropic | openai |
|---|---|---|
| 同轮多步工具循环 | 2 次 tool call + 最终回答 | 同 |
| 13,500 字符 reasoning 输入增量 | **+3,001** | **+3,001** |
| 去掉 step1 reasoning（热历史 / 冷 system，真实 id） | 200 | 200 |
| 伪造 tool_call 且缺失 reasoning | **400** | **400** |
| 伪造 tool_call 且带 reasoning / 空 reasoning | 200 / 200 | 200 / 200 |
| 普通 turn 缺 reasoning（请求带 tools） | 200 | 200 |
| thinking signature 五变体 | 全部 200，输入 3,350（篡改文本 3,354） | — |

400 错误：Anthropic `content[].thinking must be passed back`；OpenAI `reasoning_content must be passed back`。
服务端只检查字段是否存在，空字段可通过；signature 不校验。

## 3. 计费（实验 3）

| 度量 | anthropic | openai |
|---|---|---|
| 无 tools：13.5k 字符 reasoning 输入增量 | **0** | **0** |
| 有 tools：同一块输入增量 | **+3,001** | **+3,001** |
| 生成 reasoning 的 output tokens | 147（含 thinking） | 214（reasoning_tokens 212） |
| 相同请求第二次（prompt cache） | 输入 3,351，命中 3,200 | 同 |
| 第二次估算费用 | $0.0000669（首次 $0.0010077） | 同 |

## 4. 行为侧可见性（实验 4）

| 接口 | 有 tools 命中 | 无 tools 命中 |
|---|---|---|
| anthropic | 5/5 | 0/3 |
| openai | 4/5 | 0/3 |

## 5. 前几轮 tool reasoning 在多轮中的沿用（实验 5）

| 度量 | anthropic | openai |
|---|---|---|
| 第 2 轮带 tools，canary 复述 | 2/2 | 2/2 |
| 第 2 轮不带 tools，canary 复述 | 0/2 | 0/2 |
| 追加 3k 块（带 tools）输入增量 | **+3,001** | **+3,001** |
| 前一轮 tool reasoning 本身占的输入 tokens | **997** | **351** |
| 无 tools 请求输入增量 | **0** | **0** |
| 第 3 轮仍可见 / 仍计费 | 是 / 997 | 是 / 351 |

## 6. 已完成回答的 reasoning 是否带入下一轮（实验 6）

| 度量 | anthropic | openai |
|---|---|---|
| 第 2 轮带 tools，canary 复述 | 2/2 | 2/2 |
| 第 2 轮不带 tools，canary 复述 | 0/2 | 0/2 |
| 追加 3k 块（带 tools）输入增量 | **+3,001** | **+3,001** |
| 已完成轮 reasoning 本身占的输入 tokens | **123** | **122** |
| 第 3 轮仍可见 / 仍计费 | 是 / 123 | 是 / 122 |

## 7. thinking 开关矩阵（实验 7）

| 当前请求 | anthropic 输入增量 | openai 输入增量 |
|---|---|---|
| thinking 开 + 带 tools | **+3,001** | **+3,001** |
| thinking 开 + 不带 tools | 0 | 0 |
| thinking 关 + 带 tools | 0 | 0 |
| thinking 关 + 不带 tools | 0 | 0 |

## 8. 手动篡改历史 reasoning（实验 8）

| 条件 | anthropic | openai |
|---|---|---|
| original 命中旧值 A | **6/6** | **6/6** |
| modified 命中新值 B | **6/6** | **6/6** |
| modified 命中旧值 A | 0/6 | 0/6 |
| stripped | 0/4（答 none） | 0/4（答 none） |
| modified + 无 tools | 0/4 | 0/4 |
| modified + thinking 关 | A/B/C 随机猜 | A/B/C 随机猜 |
| modified + 无 signature | 4/4 命中 B | 4/4 命中 B |
| 等长替换的 token 差 | +1 | -4 |

## 9. 仅在 reasoning 中注入意图（实验 9）

### 9.1 6 次/条件

| 条件 | anthropic | openai |
|---|---|---|
| 无意图（基线） | 户外 6/6 | 户外 6/6 |
| 注入看展意图 | 看展 **6/6** | 看展 **6/6** |
| 注入做饭意图 | 做饭 **6/6** | 做饭 **6/6** |
| 做饭意图 + 无 tools | 户外 4/4 | 户外 4/4 |
| 做饭意图 + thinking 关 | 户外 4/4 | 户外 4/4 |

### 9.2 1000 次/条件并行复测（有效回答概率）

| 接口 | 设置 | 有效回答 | 看展 | 做饭 | 户外 | no_answer | tool_call |
|---|---|---|---|---|---|---|---|
| anthropic | no_intent | 1000 | 0.7% | 0.2% | **98.2%** | 0 | 0 |
| anthropic | intent_exhibition | 979 | **100.0%** | 0 | 0 | 21 | 0 |
| anthropic | intent_cooking | 978 | 0 | **99.6%** | 0.4% | 22 | 0 |
| anthropic | intent_cooking_no_tools | 1000 | 0.3% | 0.1% | **98.7%** | 0 | 0 |
| anthropic | intent_cooking_thinking_off | 974 | 7.2% | 0.1% | **86.3%** | 0 | 26 |
| openai | no_intent | 1000 | 1.0% | 0 | **97.4%** | 0 | 0 |
| openai | intent_exhibition | 983 | **100.0%** | 0 | 0 | 17 | 0 |
| openai | intent_cooking | 989 | 0 | **99.6%** | 0.3% | 11 | 0 |
| openai | intent_cooking_no_tools | 1000 | 0.5% | 0.1% | **98.2%** | 0 | 0 |
| openai | intent_cooking_thinking_off | 977 | 11.6% | 0.1% | **85.8%** | 0 | 23 |

意图条件相对基线的效应：看展 +99.3% / +99.0%，做饭 +99.4% / +99.6%（p≈0）；
控制组差值 ±0.1% 内（p=0.32~0.58）。意图提及率：意图条件 94.9%~97.6%，基线 15.9%~24.2%，thinking 关为 0。

## 10. DSH 实际发送路径验证

- 源码审计：`agent-loop` → `session.deriveMessages()` → `llm-deepseek/serialize.ts` → `/anthropic/v1/messages`；内置插件没有 reasoning 过滤器。
- 隔离 DSH 实例 + 本地代理（port 18080）抓到 6 个请求：主 agent [3,5,6]，session-title [2,4]。
- 请求 5/6 分别带回第 1 步、第 1+2 步 assistant 的 thinking（211/0 字符，均带 signature）；
  与 DSH 会话日志逐块比较 SHA 完全一致，canary `3F9A2C7E` 出现在回传的 thinking 中。
- 顶层多出的 `dsh_plugin_packages` / `dsh_session_log` 是请求扩展字段，不修改 messages。
- 实际 profile 的 `dsh-reasoning-distill` 当前 `enabled: false`；如果打开，超过 4000 token 的工具步骤 reasoning 会被有意替换。

## 11. 复现

```bash
cd deepseek-reasoning-experiment
pip install -r requirements.txt
export DEEPSEEK_API_KEY=你的有效key
python3 run_all.py
python3 exp9_parallel.py --trials 1000 --concurrency 300 --surface both
python3 analyze_exp9_parallel.py
python3 dsh_verify/verify_capture.py
```

注意：`dsh_verify` 的代理/实例步骤需要 Node/pnpm 与 DSH checkout，见 [dsh_verify/README.md](dsh_verify/README.md)。

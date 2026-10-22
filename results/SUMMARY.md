# 实验结果汇总（自动生成）

- 生成时间：2026-10-22T20:19:42.045147+08:00
- 模型：deepseek-flash，reasoning effort：high
- 当前计费档：off-peak（低谷）

## 每个实验的请求与费用

| 实验 / 接口 | 用例数 | 成功 | 预期错误 | 输入 tokens | 输出 tokens | 估算费用 USD |
|---|---|---|---|---|---|---|
| exp1_no_tools_multiturn/anthropic | 10 | 10 | 0 | 1240 | 1109 | 0.001703 |
| exp1_no_tools_multiturn/openai | 9 | 9 | 0 | 1099 | 1068 | 0.001611 |
| exp2_tools_multistep/anthropic | 18 | 17 | 1 | 26114 | 780 | 0.004330 |
| exp2_tools_multistep/openai | 13 | 12 | 1 | 10348 | 1068 | 0.003483 |
| exp3_billing/anthropic | 9 | 9 | 0 | 10706 | 320 | 0.002580 |
| exp3_billing/openai | 9 | 9 | 0 | 10704 | 307 | 0.002564 |
| exp4_visibility/anthropic | 9 | 9 | 0 | 3399 | 1675 | 0.003030 |
| exp4_visibility/openai | 9 | 9 | 0 | 3793 | 2751 | 0.004439 |
| exp5_multiturn_tool_reasoning/anthropic | 15 | 15 | 0 | 20746 | 3183 | 0.005904 |
| exp5_multiturn_tool_reasoning/openai | 15 | 15 | 0 | 15807 | 1962 | 0.004349 |
| exp6_completed_answer_reasoning/anthropic | 14 | 14 | 0 | 12330 | 1704 | 0.003900 |
| exp6_completed_answer_reasoning/openai | 14 | 14 | 0 | 11736 | 1455 | 0.003536 |
| exp7_thinking_gate/anthropic | 4 | 4 | 0 | 3712 | 29 | 0.000537 |
| exp7_thinking_gate/openai | 4 | 4 | 0 | 3712 | 33 | 0.000087 |
| exp8_reasoning_tamper/anthropic | 31 | 31 | 0 | 15849 | 4877 | 0.004080 |
| exp8_reasoning_tamper/openai | 31 | 31 | 0 | 16509 | 4985 | 0.003981 |
| exp9_intent_injection/anthropic | 29 | 29 | 0 | 11765 | 4521 | 0.003706 |
| exp9_intent_injection/openai | 29 | 29 | 0 | 11185 | 4512 | 0.003614 |
| **合计** | 272 | - | 2 | 190754 | 36339 | **0.057433** |

## 关键结论指标

### 无 tools：被忽略的 reasoning 是否计费

| 接口 | 3.3k token 合成 reasoning 块带来的输入 token 增量 |
|---|---|
| anthropic | 0 |
| openai | 0 |

### 有 tools：reasoning 是否进入上下文并计费

| 接口 | 3.3k token 合成 reasoning 块带来的输入 token 增量 |
|---|---|
| anthropic | 3001 |
| openai | 3001 |

### 行为侧可见性（同一 canary，重复试验）

| 接口 | 有 tools 命中 | 无 tools 命中 |
|---|---|---|
| anthropic | 5/5 | 0/3 |
| openai | 4/5 | 0/3 |

### 多轮后续轮次：前几轮 tool reasoning 是否沿用并计费

| 接口 | turn2 有 tools 命中 | turn2 无 tools 命中 | 追加 3k 块增量（有 tools） | 工具轮 reasoning 计费 tokens（turn2） | 无 tools 请求增量 | turn3 仍可见 | turn3 仍计费 tokens |
|---|---|---|---|---|---|---|---|
| anthropic | 2/2 | 0/2 | 3001 | 997 | 0 | True | 997 |
| openai | 2/2 | 0/2 | 3001 | 351 | 0 | True | 351 |

### 模型已完成后，新一轮用户提问是否仍带上该轮 reasoning

| 接口 | 带 tools 命中 | 无 tools 命中 | 追加 3k 块增量（有 tools） | 已完成轮 reasoning 计费 tokens | 无 tools 增量 | turn3 仍可见 | turn3 仍计费 tokens |
|---|---|---|---|---|---|---|---|
| anthropic | 2/2 | 0/2 | 3001 | 123 | 0 | True | 123 |
| openai | 2/2 | 0/2 | 3001 | 122 | 0 | True | 122 |

### thinking 开关：历史 reasoning 的采用条件（2x2）

| 当前请求 | anthropic 输入增量 | openai 输入增量 |
|---|---|---|
| thinking 开 + 带 tools | 3001 | 3001 |
| thinking 开 + 不带 tools | 0 | 0 |
| thinking 关 + 带 tools | 0 | 0 |
| thinking 关 + 不带 tools | 0 | 0 |

### 手动篡改历史 reasoning 后的下一轮回答（实验 8）

| 接口 | 原始 reasoning 命中 A | 篡改后命中 B | 去掉 reasoning | 无 tools（篡改后） | thinking 关（篡改后） | 无 signature（篡改后） | 文本长度同，token 差 |
|---|---|---|---|---|---|---|---|
| anthropic | 6/6 | 6/6 | 0/4 | ['none', 'none', 'none', 'none'] | ['C2680CCF', 'A3F09B7C', 'DFB5C3A4', 'A3F09B7C'] | 4/4 | 1 |
| openai | 6/6 | 6/6 | 0/4 | ['none', 'none', 'none', 'none'] | ['3F9A2C7D', '13FC05BC', '2E9C1636', '13FC05BC'] | 4/4 | -4 |

### 仅在 reasoning 中注入意图，开放问题是否体现（实验 9）

| 接口 | 无意图（基线） | 注入“看展”意图 | 注入“做饭”意图 | 做饭意图 + 无 tools | 做饭意图 + thinking 关 | 两句意图的 token 差 |
|---|---|---|---|---|---|---|
| anthropic | 看展 0/6，做饭 0/6，户外 6/6 | 看展 6/6，做饭 0/6，户外 0/6 | 看展 0/6，做饭 6/6，户外 0/6 | 看展 0/4，做饭 0/4，户外 4/4 | 看展 0/4，做饭 0/4，户外 4/4 | 4 |
| openai | 看展 0/6，做饭 0/6，户外 6/6 | 看展 6/6，做饭 0/6，户外 0/6 | 看展 0/6，做饭 6/6，户外 0/6 | 看展 0/4，做饭 0/4，户外 4/4 | 看展 0/4，做饭 0/4，户外 4/4 | 4 |

### 缺失 reasoning 的 tool_calls turn（伪造的冷历史）

| 接口 | HTTP | 错误信息 |
|---|---|---|
| anthropic | 400 | {"error":{"message":"The `content[].thinking` in the thinking mode must be passed back to the API. (request_id: 96e290ed-b506-4790-8b5f-eaaa58a34125)","type":"i |
| openai | 400 | {"error":{"message":"The `reasoning_content` in the thinking mode must be passed back to the API. (request_id: 27348b4e-8230-4700-ad74-022b9db20518)","type":"in |

### Anthropic thinking signature 是否校验

| 变体 | 请求成功 | 总输入 tokens |
|---|---|---|
| signature_signed | True | 3350 |
| signature_absent | True | 3350 |
| signature_bogus | True | 3350 |
| signature_empty | True | 3350 |
| signature_tampered_text | True | 3354 |

### 生成的 reasoning（输出侧）计费

| 接口 | 输出 tokens | reasoning 字符数 | 上报 reasoning tokens | 费用 USD |
|---|---|---|---|---|
| anthropic | 147 | 565 | None | 0.000204 |
| openai | 214 | 899 | 212 | 0.000284 |

### Prompt cache 对 reasoning 前缀的影响

| 接口 | 首次输入 | 首次费用 USD | 二次输入 | 二次命中 | 二次费用 USD |
|---|---|---|---|---|---|
| anthropic | 3351 | 0.001008 | 3351 | 3200 | 0.000067 |
| openai | 3351 | 0.001008 | 3351 | 3200 | 0.000067 |

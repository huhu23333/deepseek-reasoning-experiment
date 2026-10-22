# DSH 代理抓包验证

- 时间：2026-10-22T19:57:27.138238+08:00
- 代理请求数：6（主 agent：[3, 5, 6]，session-title：[2, 4]）
- 会话 assistant 消息数：2

## assistant 历史逐字节比对（DSH 会话日志 vs 代理抓到的请求体）

| 请求 | turn/step | thinking 字符 | signature | 完全一致 |
|---|---|---|---|---|
| 5 | 1/1 | [211] | [True] | True |
| 6 | 1/1 | [211] | [True] | True |
| 6 | 1/2 | [0] | [True] | True |

## 检查项

- 第一次 assistant 消息在两次后续请求中完全相同：True
- canary 在会话 thinking 中：True
- canary 在被回传的 thinking 中：True
- 所有 assistant 历史逐块一致：True
- 主请求顶层字段齐全：True
- 额外顶层字段：['dsh_plugin_packages', 'dsh_session_log']（大小 {'dsh_plugin_packages': 6416, 'dsh_session_log': 23339}）
- tools 数量/字段：24 / ['description', 'input_schema', 'name']
- thinking：{'type': 'enabled'}；output_config：{'effort': 'high'}
- session-title 请求 thinking 关闭且无 tools：True
- session-title 请求无 assistant 历史：True

## 与实验客户端的线上格式对比

| 项目 | DSH 实测 | 本次实验客户端 |
|---|---|---|
| 端点 | /anthropic/v1/messages | 同 |
| 顶层字段 | model, stream, max_tokens, messages, system, thinking, output_config, tools + 两个扩展字段 | 同前八项 |
| assistant reasoning | content 块 {type: thinking, thinking, signature} | 同 |
| assistant text/tool | {type: text} / {type: tool_use, id, name, input} | 同 |
| tools 定义 | {name, description, input_schema} | 同 |
| tool_result | user content {type: tool_result, tool_use_id, content} | 同 |

结论：DSH 主 agent 请求的 assistant 历史与 DSH 持久化日志逐块完全一致；
上一轮（含同一轮的工具步骤）的 thinking 文本和 signature 原样出现在后续请求体中。

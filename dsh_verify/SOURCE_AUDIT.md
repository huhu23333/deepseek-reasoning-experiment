# DSH 发送路径源码审计

目标：确认 DSH（deepseek-official 路由，Anthropic Messages 接口）在请求里是否原样带上历史 reasoning，
以及内置插件/当前 profile 是否会在发送前改写或丢弃它。

## 1. 请求构造

- \`packages/core/agent-loop/src/agent.ts:671\`：每次模型请求从 \`session.deriveMessages()\` 取消息；
  \`:682\` 把 \`header.tools\` 原样传给 adapter。
- \`packages/core/session/src/index.ts:925\`：\`deriveMessages()\` 按 surface 节点顺序投影事件；
  \`:949\` \`deriveEventMessage\` 调用 \`surfaceManager\` 的消息投影。
  assistant/message 的 \`content\` 里包含 \`reasoning\` / \`text\` / \`tool-call\` 块；
  当前版本没有对 \`reasoning\` 做过滤的函数（只有 image-offload 注册了 message projection）。
- 会话日志中的 assistant 消息（\`session.v4.jsonl\`）直接保存 reasoning 文本和
  \`source.replayState.blocks[].signature\`，见 \`packages/llm/llm-deepseek/src/replay.ts\`。

## 2. Provider 序列化

\`packages/llm/llm-deepseek/src/serialize.ts\`：

- assistant 块映射：
  - \`reasoning\` → \`{ type: "thinking", thinking: text, signature?: ... }\`
  - \`text\` → \`{ type: "text", text }\`
  - \`tool-call\` → \`{ type: "tool_use", id, name, input }\`
- user / tool-result 内的 reasoning、tool-call 会被省略（协议要求，不影响 assistant 历史）。
- 请求体字段：\`model, stream, max_tokens, messages, system, thinking, output_config, tools\`；
  thinking 开启且 effort 非 off 时带 \`output_config.effort\`。

\`packages/llm/llm-deepseek/src/adapter.ts\`：fetch 到 \`${baseURL}/v1/messages\`，
发送 \`content-type\`、\`accept: text/event-stream\`、\`x-api-key\`、\`anthropic-version\` 等头；
SSE 响应由 \`translate.ts\` 解析，\`signature_delta\` 写入 replay 元数据。

## 3. 内置插件影响

默认 base 组合（\`packages/bundle/base/cordis.patch.yml\`）里可能接触消息的插件：

| 插件 | 是否改 reasoning | 说明 |
|---|---|---|
| \`compaction-basic\` | 仅在压缩触发时整体替换旧区间 | 上下文接近阈值才运行；短会话不触发。压缩后旧 reasoning 随旧消息被摘要取代 |
| \`compaction-tool-result-pruner\` | 否 | 只裁剪超长 tool result（默认 8192 字符） |
| \`compaction-image-offload\` | 否 | 只替换图片为占位符（message projection 仅 image） |
| \`session-reference\` | 否（只影响“引用其他会话”） | \`src/projection.ts:37\` 明确排除 tools/reasoning；当前会话自身不受影响 |
| \`agent-instructions\`/\`time-context\`/\`skill\` 等 | 否 | 只注入 user/system 文本 |
| \`dsh_session_log\` / \`dsh_plugin_packages\` 请求扩展 | 否 | 追加顶层 body 字段，不修改 messages |
| \`session-title-llm\` | 不适用 | 单独请求，thinking 强制 off、不带 tools、只包含选中的 user 消息，没有 assistant 历史 |

结论：**内置 DSH 路径没有 reasoning 过滤器**；assistant 历史中的 reasoning 会原样进入 messages。

> 注意：当前 checkout 不是纯净上游。\`git status\` 显示
> \`packages/core/session/src/index.ts\`、\`surface.ts\`、\`session-persistence-jsonl/*\` 等被修改，
> 其中包含 \`rewriteReasoningBlock()\`（由第三方 \`dsh-reasoning-distill\` 的 in-place 补丁加入）。
> 该方法没有内置调用方；不启用该插件时不会执行。

## 4. 当前真实 profile 的第三方插件

真实 Web profile（\`~/.dsh/profiles/web/cordis.yml:3095\` 起）额外挂载了第三方插件：

- \`dsh-reasoning-distill\`：**会改写历史 reasoning**（当启用且超过阈值时）。
  - 当前配置：\`enabled: false\`（总开关关闭）；\`models.deepseek-flash.enabled: true\`、
    \`thresholdTokens: 4000\`、\`rewriteBackend: in-place\`、
    \`includeToolResults: false\`、\`includeFinalTurns: false\`。
  - 也就是说：如果打开总开关，DeepSeek Flash 的**工具调用步骤** reasoning 超过 4000 token 时，
    会在下一次请求前被蒸馏文本替换；最终回答轮和子代理默认不改。
  - \`~/.dsh/reasoning-distill/\` 中有历史蒸馏记录（含 originalHash、distilledText、branch 信息），
    说明它曾在某些会话中实际生效过。
- \`dsh-message-edit\`：装为依赖，但当前 \`rewriteBackend: in-place\`，没有作为后端启用。
- 其他 profile 插件（UI、市场、皮肤、context 等）未发现顶层调用
  \`rewriteReasoningBlock\` / \`registerMessageProjection\` 的代码；但第三方插件不受 DSH 内置保证，
  启用新插件后应重新验证。

## 5. 隔离实例与代理实证

- 新 DSH_HOME：\`dsh_verify/home\`（全新，headless profile 未挂载 reasoning-distill）。
- 代理：\`dsh_verify/proxy.py\` 监听 \`127.0.0.1:18080\`，把
  \`DEEPSEEK_BASE_URL=http://127.0.0.1:18080/anthropic\` 的请求原样落盘后转发到
  \`https://api.deepseek.com/anthropic/v1/messages\`，用真实 key 替换 DSH 的 dummy key。
- 会话：用 \`pnpm dsh --profile headless --session-id ...\` 两次调用同一 session：
  第 1 轮生成 canary + 调用 bash，第 2 轮要求复述。
- 抓到的 6 个请求：主 agent [1/3/5/6]，session-title [2/4]。
  - 请求 5（工具结果后同轮下一步）：messages 含第 1 步 assistant 的 thinking（211 字符，带 signature）+ text + tool_use。
  - 请求 6（第 2 个 user turn）：messages 含第 1 步、第 2 步两个 assistant 的 thinking（211/0 字符，均带 signature），
    以及 tool result 和新 user 消息。
  - 与 \`session_6838.jsonl\` 中持久化的 assistant 消息逐块比较：thinking 文本、signature、text、tool_use
    全部完全一致（SHA 相同），见 \`VERIFICATION.md\`。
- 顶层字段差异：DSH 额外带 \`dsh_plugin_packages\`（约 6.4KB）和 \`dsh_session_log\`（约 57.8KB）；
  它们是请求扩展字段，不修改 messages，不替代 reasoning。

## 6. 结论

1. 内置 DSH（deepseek-official / Anthropic Messages）不会拦截或改写历史 reasoning；
   后续请求中的 assistant thinking 与持久化日志逐字节一致，带原 signature。
2. 线上格式与本次实验客户端一致（只多两个扩展字段）。
3. **唯一需要留意的实际配置**是第三方 \`dsh-reasoning-distill\`：当前关闭；若打开，超过 4000 token 的
   工具步骤 reasoning 会被有意替换成蒸馏版。这不是内置路径的行为，且可在设置里关闭/卸载。
4. session-title 等辅助请求 thinking off、无 tools、无历史，不涉及 reasoning 回放。

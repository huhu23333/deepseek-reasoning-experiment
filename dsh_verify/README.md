# dsh_verify

隔离 DSH 实例 + 本地 API 代理，用来验证 DSH 实际发给 DeepSeek API 的请求体。

## 文件

- \`proxy.py\`：127.0.0.1:18080 代理，落盘每个请求到 \`proxy_logs/\`，再转发到真实 API。
- \`home/\`：本次使用的隔离 DSH_HOME（headless profile）。
- \`proxy_logs/\`：6 个真实请求的 body / meta / response；\`index.jsonl\` 摘要。
- \`session_6838.jsonl\`：隔离实例的会话日志（解压后的 v4 JSONL）。
- \`verify_capture.py\`：把代理请求与会话日志逐块比对，生成 \`VERIFICATION.md\` / \`capture_report.json\`。
- \`SOURCE_AUDIT.md\`：完整源码链路与插件审计。

## 复现

终端 1：

\`\`\`bash
cd dsh_verify
python3 proxy.py
\`\`\`

终端 2：

\`\`\`bash
export DSH_HOME="$(pwd)/home"
export DEEPSEEK_API_KEY="sk-proxy-dummy"
export DEEPSEEK_BASE_URL="http://127.0.0.1:18080/anthropic"
cd /home/huhu233/otherProject/deepseek-harness
TASK1="请先在你的思考/reasoning 中生成一个 8 位十六进制随机串，按格式写下：暗号=XXXXXXXX。然后调用 bash 工具执行 date 命令；最后只回复 OK。"
TASK2="请输出你上一轮思考中生成的那个 8 位十六进制暗号；如果看不到就输出 UNKNOWN。"
OUT1=$(pnpm -s dsh --profile headless --json "$TASK1" 2>&1)
SID=$(echo "$OUT1" | head -1 | python3 -c 'import sys,json; print(json.load(sys.stdin)["sessionId"])')
pnpm -s dsh --profile headless --session-id "$SID" --json "$TASK2"
\`\`\`

然后：

\`\`\`bash
python3 dsh_verify/verify_capture.py
\`\`\`

Web 实例（另一个端口，同一隔离 HOME + 代理）：

\`\`\`bash
DSH_HOME="$(pwd)/dsh_verify/home" \
DEEPSEEK_API_KEY=sk-proxy-dummy \
DEEPSEEK_BASE_URL=http://127.0.0.1:18080/anthropic \
pnpm --dir /home/huhu233/otherProject/deepseek-harness -s dsh --profile web --port 3099 --no-open
\`\`\`

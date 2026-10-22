# 实验数据清单与格式

> `code/config.py` 中的 API key 已删除/吊销，详见 [SECURITY.md](SECURITY.md)。

## 目录总览

```text
deepseek-reasoning-experiment/
├── REPORT.md                     完整实验报告（含 19 节）
├── RESULTS.md                    结果总览（数据 + 结论）
├── DATA.md                       本文件：数据清单与格式
├── SECURITY.md                   key 已删除说明
├── README.md / REPORT.md / RESULTS.md / DATA.md / SECURITY.md
├── requirements.txt / .gitignore
├── code/                         Python 代码
│   ├── config.py / ds_client.py / probes.py
│   ├── exp1..exp9_intent.py      实验 1–9
│   ├── exp9_parallel.py          实验 9 并行版（1000 次/设置）
│   ├── analyze_results.py / analyze_exp9_parallel.py
│   └── run_all.py / show_exp9_transcripts.py / smoke_test.py
├── results/
│   ├── exp1..exp9_<surface>.json 主实验逐用例记录
│   ├── summary.json / SUMMARY.md 主实验汇总
│   ├── exp9_transcripts.md       实验9每个设置一段完整对话
│   └── exp9_parallel/            1000 次并行复测（原始 JSONL + 矩阵 + 统计）
├── dsh_verify/                   隔离 DSH 实例 + 代理抓包
│   ├── SOURCE_AUDIT.md           DSH 发送路径源码审计
│   ├── VERIFICATION.md           抓包与会话日志逐块比对
│   ├── proxy.py                  本地 API 代理
│   ├── proxy_logs/               6 个真实请求的 body/meta/response
│   ├── session_6838.jsonl        隔离实例会话日志
│   └── home/                     隔离 DSH_HOME（profile/session 数据）
└── session/                      会话导出目录（含彩蛋）
```

## 主实验数据（results/exp*_*.json）

共 18 个文件（9 个实验 × anthropic/openai 两个接口），每个约 3–29 KB。

结构：

```json
{
  "experiment": "exp2_tools_multistep",
  "surface": "anthropic",
  "model": "deepseek-flash",
  "effort": "high",
  "peak": true,
  "generated_at": "...",
  "cases": [
    {
      "name": "step1",
      "notes": "...",
      "ok": true,
      "usage": {"total_input_tokens": ..., "cache_read_input_tokens": ..., "output_tokens": ...},
      "cost_usd": {"input_cache_miss": ..., "input_cache_hit": ..., "output": ..., "total": ...},
      "stop_reason": "tool_use",
      "text": "...",
      "reasoning_chars": 211,
      "delta_input_tokens": 3001,
      "canary_visible": true
    }
  ]
}
```

有的用例带 `error` / `status`（预期 400 错误），有的带实验专属字段（`delta_input_tokens`、`tool_calls`、`canary_visible`、`categories`、`code` 等）。

- `results/summary.json`：所有主实验的 per-run 汇总、grand_total、关键指标（ignored_vs_billed、visibility、signature、thinking_gate、reasoning_tamper、intent_injection、multiturn_tool_reasoning、completed_answer_reasoning 等）。
- `results/SUMMARY.md`：由 `analyze_results.py` 自动生成的可读汇总。
- `results/exp9_transcripts.md`：实验 9 五个设置各一段完整真实对话（含 thinking / 可见回答 / usage）。

## 实验 9 并行数据（results/exp9_parallel/）

| 文件 | 内容 | 规模 |
|---|---|---|
| `{surface}_{setting}.jsonl` | 每个设置每个接口 1000 条完整请求/响应 | 10 文件，10,000 行，约 26 MB |
| `probability_matrix_refined.{md,csv,json}` | 离线重分类后的概率矩阵（最终版） | 10 行 |
| `probability_matrix.{md,csv,json}` | 初版分类矩阵（未剔除 no_answer/tool_call） | 10 行 |
| `statistics.md` | Wilson 95% CI、两比例检验、意图提及率、示例 | — |
| `metadata.json` / `meta_*.json` | 运行参数、token/费用汇总、共享 turn-1 | — |

每条 JSONL 记录的字段：

```json
{
  "ok": true,
  "surface": "anthropic",
  "setting": "intent_cooking",
  "trial": 43,
  "system": "exp9-parallel-...",
  "messages": ["完整请求 messages：system + 第1轮 user/assistant(thinking+signature) + 第2轮 user"],
  "response": {
    "thinking": "模型本轮 reasoning 全文",
    "text": "可见回答全文",
    "content_blocks": ["原始 content blocks"],
    "stop_reason": "end_turn",
    "request_id": "...",
    "usage": {...}
  },
  "category": "cooking",
  "counts": {"exhibition": 0, "cooking": 3, "outdoor": 0},
  "attempt": 1,
  "elapsed_s": 6.181,
  "timestamp": "..."
}
```

## DSH 验证数据（dsh_verify/）

| 文件 | 内容 |
|---|---|
| `proxy_logs/000N-body.json` | DSH 实际发出的完整请求 body（6 个请求） |
| `proxy_logs/000N-meta.json` | 路径、headers（key 已 redacted）、消息块摘要 |
| `proxy_logs/000N-response.txt` | 上游响应（SSE/headers） |
| `proxy_logs/index.jsonl` | 6 个请求的摘要索引 |
| `session_6838.jsonl` | 隔离实例的会话日志（zstd 解压后的 v4 JSONL） |
| `capture_report.json` | 代理请求 vs 会话日志的逐块比对结果 |
| `VERIFICATION.md` | 可读比对报告 |
| `SOURCE_AUDIT.md` | 源码链路与插件审计 |

## 复算命令

```bash
python3 code/analyze_results.py          # 重新生成 results/summary.json 与 SUMMARY.md
python3 code/analyze_exp9_parallel.py    # 重新生成 refined 矩阵与 statistics.md
python3 code/exp9_parallel.py --trials 1000 --concurrency 300 --surface both  # 重跑并行实验
python3 dsh_verify/verify_capture.py      # 重新比对抓包与会话日志
```

## 说明

- 所有费用字段都是 usage × 官方价估算；不是账单明细。
- 主实验有 2 个 `ok: false` 是预期复现的 400；不是失败数据。
- 并行数据里 `no_answer` 是 `max_tokens` 截断，`tool_call` 是 thinking 关闭时模型误调工具；两者都在矩阵中单列。
- 原始数据保留完整请求/响应内容，未做删减（除代理日志对认证头 redacted）。

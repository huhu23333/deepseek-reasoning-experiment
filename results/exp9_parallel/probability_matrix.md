# 实验 9 并行版：概率矩阵

- 生成时间：2026-10-22T19:39:32.517693+08:00
- 模型：deepseek-flash，effort=high，接口：anthropic / openai
- 每个设置每接口目标次数：1000
- 计费档：off-peak，费用为按 usage × 官方价估算

| 接口 | 设置 | 带 tools | thinking | 完成 | 失败 | exhibition | cooking | outdoor | other | 费用 USD |
|---|---|---|---|---|---|---|---|---|---|---|
| anthropic | no_intent | True | True | 1000 | 0 | 7 (0.007) | 2 (0.002) | 982 (0.982) | 9 (0.009) | 0.0909 |
| anthropic | intent_exhibition | True | True | 1000 | 0 | 979 (0.979) | 0 (0.000) | 0 (0.000) | 21 (0.021) | 0.1494 |
| anthropic | intent_cooking | True | True | 1000 | 0 | 0 (0.000) | 932 (0.932) | 4 (0.004) | 64 (0.064) | 0.1569 |
| anthropic | intent_cooking_no_tools | False | True | 1000 | 0 | 3 (0.003) | 1 (0.001) | 987 (0.987) | 9 (0.009) | 0.0866 |
| anthropic | intent_cooking_thinking_off | True | False | 1000 | 0 | 62 (0.062) | 1 (0.001) | 840 (0.840) | 97 (0.097) | 0.0454 |
| openai | no_intent | True | True | 1000 | 0 | 10 (0.010) | 0 (0.000) | 974 (0.974) | 16 (0.016) | 0.0954 |
| openai | intent_exhibition | True | True | 1000 | 0 | 982 (0.982) | 0 (0.000) | 0 (0.000) | 18 (0.018) | 0.1623 |
| openai | intent_cooking | True | True | 1000 | 0 | 0 (0.000) | 959 (0.959) | 3 (0.003) | 38 (0.038) | 0.1521 |
| openai | intent_cooking_no_tools | False | True | 1000 | 0 | 5 (0.005) | 1 (0.001) | 982 (0.982) | 12 (0.012) | 0.0797 |
| openai | intent_cooking_thinking_off | True | False | 1000 | 0 | 108 (0.108) | 0 (0.000) | 837 (0.837) | 55 (0.055) | 0.0457 |

概率 = 该设置下该类别次数 / 完成次数。

# Benchmark results

This run used the frozen 50-task dataset dated 2026-09-23. Both full runs used the same task-file SHA-256 (`22118bffda875fbe250d367c597a087c2331b7d9cb0c25c58b9bda774054f95c`), Browsr `standard` mode with short descriptions, 3,000 output tokens, and ID links. Ollama was called through its OpenAI-compatible Chat Completions endpoint with `reasoning_effort=none` and a 2,048-token response cap. Each Browsr child used a run-isolated cache. Sources and expected answers are listed in [`REFERENCES.md`](REFERENCES.md).

| Model | Automated checker + complete response | Tasks with tools | Tool calls / logged | Total LLM tokens / task | Mean completion tokens / response | Browsr tool response tokens (estimated) | Call latency p50 / p95 | Elapsed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `gemma4:e2b` | 39/50 (78%) | 31/50 | 34 / 34 | 1,071 | 105.1 | 730.9 | 1.20s / 3.02s | 4m 44s |
| `gemma4:e4b` | 40/50 (80%) | 31/50 | 61 / 61 | 2,191 | 109.6 | 742.0 | 1.27s / 2.88s | 11m 33s |

For both runs, every traced tool call had a matching Browsr log row, all logged outcomes were non-`bad_input`, normalized actions agreed with the two-tool selection, and no LLM transport errors or length-truncated final answers occurred. The correction-free rate was 100% in both runs. Call outcome and selection metrics apply only to the 34 and 61 logged tool calls respectively; the `tasks with tools` column shows that each model used Browsr on 31 tasks.

Site-access blocks were recorded as normal tool outcomes: 1 of 34 calls for e2b and 6 of 61 for e4b. The remaining calls returned `ok`; the absence of LLM transport errors does not mean every requested site was accessible.

“Automated checker + complete response” is not a human fact-check score. The checker requires a configured regex or substring and the model must finish without a transport or length error. It can miss semantically correct renderings: for example, `f18` expects `H2O`/`H₂O`, while one answer formats the same formula as `\text{H}_2\text{O}`. It can also reject an incomplete answer appropriately: for `f02`, the answer linked to 403/404 sources but did not explain both codes. Some current-information checks require an exact official-site URL; a correct version without that URL can fail. Review the question, answer, and referenced sources before interpreting individual misses.

The 27B comparison was not run at the user's direction because it is impractically slow on this Mac. The 36-variant matrix was not run; these are baseline model comparisons only.

Individual reports:

- [Gemma 4 e2b JSON](results/gemma4-e2b-standard/gemma4-e2b-standard.json) · [Markdown](results/gemma4-e2b-standard/gemma4-e2b-standard.md)
- [Gemma 4 e4b JSON](results/gemma4-e4b-standard/gemma4-e4b-standard.json) · [Markdown](results/gemma4-e4b-standard/gemma4-e4b-standard.md)
- [Three-task Gemma 4 e2b smoke JSON](results/smoke-verified/gemma4-e2b-standard-short-3000-id.json) · [Markdown](results/smoke-verified/gemma4-e2b-standard-short-3000-id.md)

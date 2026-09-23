# Browsr benchmark runner

`tasks.jsonl` contains 50 bilingual questions covering factual recall, current facts, and comparisons. It is a frozen snapshot dated 2026-09-23; each expected answer and its official source are recorded in [`REFERENCES.md`](REFERENCES.md). A reported success means only that the final response matched the task's `regex` or substring check and finished without a transport or length-truncation error. It is an automated checker score, not a human judgment that the answer is factually correct or complete. Review the linked evidence and answer text when interpreting scores, and update the task snapshot and references together when refreshing time-sensitive items.

The runner talks to an OpenAI-compatible Chat Completions endpoint and to Browsr's REST interface. It forwards each model-produced `function.arguments` string as a string in `POST /call`, assigns one fresh `X-Session` per task, and stops after eight tool-calling rounds by default. It writes `{model}-{variant}.json` and `.md` under `eval/results/` with the answers, tool traces, token usage reported by the model endpoint, and metrics joined to `calls.jsonl` records by session ID.

## Run

Install the project and its dependencies in the active Python environment, then set an endpoint URL. An API key is optional; if needed, provide `OPENAI_API_KEY` or `LLM_API_KEY`. The key is sent only as an Authorization header and is never printed or written to the report.

```sh
export LLM_URL=http://127.0.0.1:8000/v1/chat/completions
export OPENAI_API_KEY=your-local-or-provider-key
python eval/run.py --model example-model --variant standard-short-3000-id
```

The system prompt includes the benchmark date, tells the model to browse, and treats tool text as untrusted data. For local endpoints with model-specific controls, add `--max-completion-tokens 1024` to cap each answer. OpenAI-compatible providers can use `--reasoning-effort low`; Ollama documents `none`, `low`, `medium`, `high`, and `max` for its compatible endpoint, for example `--reasoning-effort none`. Endpoint-specific fields can be sent with a repeated `--llm-param KEY=JSON_VALUE`. These generation controls affect the LLM response budget; Browsr's `output.max_tokens` remains controlled by the selected variant. See [Ollama OpenAI compatibility](https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx).

Without `--browsr-url`, the runner starts `python -m browsr serve` on a local port using the chosen variant and writes its call log under the selected output directory. It terminates that child server after the run. For an existing server, pass `--browsr-url http://127.0.0.1:8765`; the runner checks that `/tools` has the expected tool names and descriptions. The current Browsr API does not expose its `output.max_tokens` or `tools.link_style`, so the runner prints those two settings and requires that the existing server was started with the selected variant.

Useful options:

```text
--variant standard-short-3000-id   # variant name or a TOML path
--browsr-url URL                   # connect to an existing Browsr server
--max-steps 8                      # tool-calling rounds per task
--max-completion-tokens 512        # optional generation cap per response
--reasoning-effort low             # optional OpenAI-compatible reasoning setting
--llm-param think=true             # arbitrary endpoint-specific JSON field
--tasks eval/tasks.jsonl           # alternate JSONL task file
--calls-log PATH                  # calls.jsonl to merge by X-Session
--output-dir eval/results          # output directory
```

There are 36 variants covering the full design matrix: standard/single mode, short/medium/long descriptions, 1,000/3,000/6,000 output tokens, and `id`/`url` link style. The `standard` and `single` aliases select short descriptions, 3,000 tokens, and ID links.

## Reading metrics

The report separates `checker_match` from `success`; a length-truncated answer or tool/LLM transport error cannot count as a success even if its visible text matches. It also records how many tasks used at least one tool, each model tool result and latency, finish reasons, task checks, request settings, and the task-file hash. Tool-call correction, `bad_input` outcomes, selected action, and call latency come from Browsr's JSONL log and are attached only when the session matches. Transcript calls without a matching call-log row are counted and reported. Token metrics use the endpoint's reported `usage` values. If a server's call log is inaccessible or was rotated, those metrics are marked unavailable rather than inferred. For the standard two-tool mode, tool-selection accuracy checks whether the requested `search`/`open` tool agrees with Browsr's normalized `action`; a single-tool `web` call is excluded from that particular metric because one tool name cannot express the requested action.

Current-version and office-holder questions can become stale. Review or refresh those expected-answer checks before using this set for a future release comparison. Search results may also be unavailable depending on local SearXNG and public search backend health. The repository does not include model credentials or precomputed benchmark outcomes; results are created only when you run the benchmark.

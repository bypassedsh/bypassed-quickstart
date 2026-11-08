# bypassed.sh quickstart

Code for your first request to [bypassed.sh](https://bypassed.sh/?utm_source=github&utm_medium=referral&utm_campaign=quickstart-repo), plus a small coding agent that works on a failing pagination test through the API. Standard library only: `curl` and Python 3.9 or newer, no packages.

bypassed.sh serves the OpenAI-compatible Chat Completions API. The unlocked models have abliterated weights, so they decline far fewer requests than the standard releases. They still refuse sometimes, and the [terms](https://bypassed.sh/terms) still apply. [Abliterated vs jailbroken](https://bypassed.sh/guides/abliterated-vs-jailbroken?utm_source=github&utm_medium=referral&utm_campaign=quickstart-repo) explains the difference.

## 1. Get a key

Sign up at [bypassed.sh](https://bypassed.sh/?utm_source=github&utm_medium=referral&utm_campaign=quickstart-repo), add credit, and create a key on the Keys page. Credit is prepaid. Keep the key out of your shell history and out of this directory's git history:

```sh
read -rs BYPASSED_API_KEY && export BYPASSED_API_KEY   # paste, then Enter
```

Or copy `.env.example` to `.env` (ignored by git) and load it with `set -a; . ./.env; set +a`.

Checking the key is free. Listing models uses no credit:

```sh
curl -sS -H "Authorization: Bearer $BYPASSED_API_KEY" https://api.bypassed.sh/v1/models
```

## 2. First request

```sh
./first-request.sh
python3 first_request.py
python3 first_request.py --stream "Explain Python's GIL in two sentences."
```

Each sends one request with `max_tokens` 200 and `reasoning_effort` `low` to `glm-5.3-flash-unlocked`.

Base URL: `https://api.bypassed.sh/v1`. Any OpenAI-compatible client works with that base URL and your key.

| Model | Input | Cached input | Output | Reasoning | Images |
| --- | --- | --- | --- | --- | --- |
| [`glm-5.3-unlocked`](https://bypassed.sh/models/glm-5-3-unlocked?utm_source=github&utm_medium=referral&utm_campaign=quickstart-repo) | $1.25 | $0.20 | $3.50 | low, high, max | no |
| [`glm-5.3-flash-unlocked`](https://bypassed.sh/models/glm-5-3-flash-unlocked?utm_source=github&utm_medium=referral&utm_campaign=quickstart-repo) | $0.15 | $0.015 | $0.55 | low, medium, high | no |
| [`deepseek-v4.1-flash-unlocked`](https://bypassed.sh/models/deepseek-v4-1-flash-unlocked?utm_source=github&utm_medium=referral&utm_campaign=quickstart-repo) | $0.20 | $0.04 | $0.75 | off, low, high, max | yes |

USD per million tokens, as of 8 November 2026. The [price list](https://bypassed.sh/?utm_source=github&utm_medium=referral&utm_campaign=quickstart-repo#models) is current and also has standard-weight models. Each unlocked model has a 1,048,576-token context and up to 131,072 output tokens.

What to know before you build on it:

- **Billing.** Reasoning tokens are billed as output. Each request holds credit for its full `max_tokens` while it runs and releases the unused part when it settles, so set `max_tokens` to what you need.
- **Streaming.** `"stream": true` returns server-sent events. The last chunk carries usage.
- **Tools.** Function tools only, executed by your code. The API never runs them. `tool_choice` accepts `auto`, `none`, `required` or a named function.
- **Not supported.** `n` other than 1, the Responses API and the Anthropic Messages API.
- **No retries.** The API never retries a request for you. A 429 `model_busy` or `model_warming` is released without charge and has a `Retry-After` header.

## 3. Coding agents

OpenCode, Cline, Aider and other tools with an OpenAI-compatible provider option work with the base URL and a model ID above. For Aider:

```sh
export OPENAI_API_BASE=https://api.bypassed.sh/v1
export OPENAI_API_KEY=$BYPASSED_API_KEY
aider --model openai/glm-5.3-flash-unlocked
```

The dashboard's **Connect** dialog has setup steps and a copyable setup prompt for each tool.

## 4. A small coding agent

`agent_demo.py` is a complete tool-calling loop. It copies `sample/` to a temporary directory, where `page_slice` has an off-by-one bug: it treats pages as 0-based, so two of five tests fail. The model gets four tools:

| Tool | Allowed |
| --- | --- |
| `list_files` | lists the two sample files |
| `read_file` | `pagination.py` or `test_pagination.py` only |
| `replace_in_file` | `pagination.py` only, one exact unique match, no imports or system names |
| `run_tests` | runs the sample's unit tests |

```sh
python3 agent_demo.py
```

The harness runs the tests itself before the first request and after the last one. "FIXED" means its own run went from failing to passing, whatever the model claims.

Limits, all enforced by the script:

- `glm-5.3-flash-unlocked`, `reasoning_effort` `low`, `max_tokens` 1000 per request, at most 8 requests. `--max-requests` (up to 12), `--max-tokens` (up to 2000) and `--model` change them.
- A default run is expected to cost a few cents at the prices above. Choosing another model or larger limits changes the cost. The report estimates cost from reported usage; the dashboard's usage page is the billing record.
- It never retries. Any HTTP error, timeout or unreadable response stops the run. A 5xx or timeout is marked "uncertain billing": check the usage page before running again.
- The key is read only from `BYPASSED_API_KEY`. It is never printed, written to the report or passed as an argument.

The model's edit runs as Python when the tests import `pagination.py`. The script limits what the model can change and rejects edits that import modules or use system names, and the tests run in an isolated interpreter with no API key in their environment. That is a guard, not a sandbox. Read the diff it prints.

Each run writes `runs/run-<time>.json`: settings, timestamps, every model turn (tool calls, finish reason, usage, request ID), every tool result except file contents, the test summaries before and after, the diff, token totals and the cost estimate. It contains no key and no account details. `"fixture": true` marks a run against a base URL other than the real API.

## Tests

```sh
python3 -m unittest discover -s tests
```

These run the agent against a scripted local server. They check the harness (tool limits, stopping, the report, key handling), not any model's ability.

## License

MIT. See [LICENSE](LICENSE).

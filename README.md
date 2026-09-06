# Vector Cannon

**Fire the cheapest capable AI model first. Escalate only when the target deserves it.**

Vector Cannon is a budget-aware, read-only model firing controller for repository analysis.

Point the same repository and prompt at multiple AI providers, cap the budget per shot, record what the model actually inspected, and compare cost against useful output instead of guessing from model reputation.

## Quick start

```bash
git clone https://github.com/nobutakayamauchi/vector-cannon.git
cd vector-cannon
python -m pip install -e .
vector-cannon providers
```

Set only the key for the provider you want to use. Never commit keys.

```bash
export UNOROUTER_API_KEY='...'
# or AI_GATEWAY_API_KEY / OPENAI_API_KEY / OPENROUTER_API_KEY /
# GEMINI_API_KEY / TOGETHER_API_KEY / FIREWORKS_API_KEY / GROQ_API_KEY
```

Discover models where the provider exposes a model catalog:

```bash
vector-cannon models --provider unorouter --contains free
vector-cannon models --provider vercel --contains astra
```

Then fire a bounded read-only repository shot:

```bash
vector-cannon fire \
  --provider unorouter \
  --model gpt-oss-120b:free \
  --repo ../your-repo \
  --prompt prompts/vector_cannon/repo_recon.md \
  --max-usd 0.25 \
  --tag free-recon-01
```

Run logs are written outside the target repository under `~/.vector-cannon/runs/` by default.

## Why

Frontier models are getting better and more expensive at the same time. Meanwhile free tiers, promotional pricing, routers, and smaller models change constantly.

Vector Cannon treats model capacity like ammunition:

1. map the target,
2. fire the cheapest plausible model,
3. measure the result and cost,
4. escalate only when a material question remains.

It is designed for experiments such as:

- Can a free or discounted model map this repository well enough?
- Is a frontier model actually finding anything the cheaper model missed?
- How much does that extra quality cost on this specific workload?
- Which model/provider should become the default route for this class of work?

## Safety boundary

Vector Cannon gives fired models **read-only repository tools only**.

It can list paths, read bounded text files, and search text. It does not expose repository write, commit, PR, deploy, shell, or arbitrary network tools to the model.

It also blocks common secret files, dependency/build directories, path traversal, out-of-root symlinks, binary files, and common credential patterns before repository text is sent upstream.

Treat this as a defensive boundary, not a formal DLP system. Do not aim it at sensitive repositories unless the selected provider is appropriate for that data.

## Providers

Current built-in lanes:

- Vercel AI Gateway
- OpenAI
- OpenRouter
- UnoRouter
- Gemini OpenAI compatibility
- Together AI
- Fireworks AI
- Groq

Provider endpoints and secret names are fixed in code. A prompt or issue body cannot redirect an API key to an arbitrary host.

UnoRouter models ending in `:free` are treated as provider-declared zero-cost models for preflight accounting. Free models may still have provider/rate limits and availability constraints.

## What a shot records

Every run records:

- provider and exact model id
- reasoning setting
- repository and prompt
- elapsed time
- model turns and tool calls
- exact files read
- input/output token counts
- cost accounting mode and pricing source
- observed/estimated cost
- final answer and stop reason

## Budget semantics

`--max-usd` is a best-effort per-shot ceiling.

Vector Cannon fails closed when it cannot establish a trusted price. Depending on the provider, cost may come from an exact balance delta, provider-reported request cost, live catalog pricing, a provider-declared free model, or explicit current token prices supplied by the operator.

The budget controller is intended to prevent careless burns, not to replace provider-side spend limits.

For providers without trusted live pricing, pass current prices explicitly:

```bash
vector-cannon fire \
  --provider openai \
  --model YOUR_MODEL_ID \
  --repo ../your-repo \
  --prompt prompts/vector_cannon/repo_recon.md \
  --input-price-per-million CURRENT_INPUT_PRICE \
  --output-price-per-million CURRENT_OUTPUT_PRICE \
  --max-usd 1.00
```

## Escalation example

After a cheap/free reconnaissance shot, escalate only if a material question remains:

```bash
vector-cannon fire \
  --provider vercel \
  --model openai/gpt-6-astra \
  --repo ../your-repo \
  --prompt prompts/vector_cannon/repo_recon.md \
  --reasoning low \
  --max-usd 1.00 \
  --tag frontier-recon-01
```

Model ids, prices, discounts, quotas, and availability change. Discover and verify the current provider state before treating an example as current truth.

## Optional GitHub Issue FIRE channel

The included `.github/workflows/vector-cannon.yml` can fire only when the repository owner opens an issue whose title starts with `[VECTOR-CANNON]`.

Store provider keys as GitHub Actions repository secrets. The workflow accepts provider/model/budget fields from the issue body, but never accepts API keys or arbitrary provider URLs there.

## Philosophy

Vector Cannon does not ask "Which AI is smartest?"

It asks:

> **What is the cheapest shot that reliably solves this target, and what extra value do I get by escalating?**

That turns model choice from branding into an observable routing problem.

## Status

Early public release candidate. Interfaces may change while provider adapters and accounting improve.

## Commercial / integration requests

If you want a new provider lane, organization-specific routing, private deployment, custom audit prompts, or integration into an existing development workflow, open a GitHub issue describing the workflow and constraints. Do not post API keys or other secrets in issues.

# Vector Cannon

**Fire the AI you need without rebuilding the route every time. Fire cheap first; escalate only when the target deserves it.**

Vector Cannon is a budget-aware, multi-provider inference firing controller.

Its first mission is simple:

> **Reduce the number of people who cannot fire the model they need.**

New models appear constantly. Provider UIs, APIs, authentication, pricing, quotas, and model catalogs differ. Subscription limits run dry. Sometimes you want one Claude/Kimi/Gemini/Astra-class shot without maintaining another monthly subscription.

Vector Cannon turns approved inference venues into reusable firing lanes, keeps per-shot budget controls, and records every shot so model choice can become evidence instead of guesswork.

## Quick start

```bash
git clone https://github.com/nobutakayamauchi/vector-cannon.git
cd vector-cannon
python -m pip install -e .
vector-cannon doctor
```

Set only the keys for the firing lanes you actually use. Never commit keys.

```bash
export AI_GATEWAY_API_KEY='...'
export OPENROUTER_API_KEY='...'
# or OPENAI_API_KEY / UNOROUTER_API_KEY / GEMINI_API_KEY /
# TOGETHER_API_KEY / FIREWORKS_API_KEY / GROQ_API_KEY
```

## 1. Check the energy lines

```bash
vector-cannon doctor
```

`READY` means the required local credential is present. `UNARMED` means Vector Cannon knows the approved lane but does not currently have its key.

List models visible through a firing lane:

```bash
vector-cannon models --provider unorouter --contains free
vector-cannon models --provider vercel --contains astra
```

Model ids, prices, discounts, quotas, and availability change. Discover and verify current provider state rather than treating an old example as permanent truth.

## 2. Direct shot — no repository required

Fire a normal bounded inference request:

```bash
vector-cannon shot \
  --provider openrouter \
  --model YOUR_MODEL_ID \
  --text 'Compare these two approaches and recommend one.' \
  --max-usd 0.50 \
  --tag trial-shot-01
```

For longer prompts:

```bash
vector-cannon shot \
  --provider vercel \
  --model openai/YOUR_MODEL_ID \
  --prompt-file ./mission.md \
  --reasoning low \
  --max-usd 1.00
```

This is the first Inference Access Layer: if an approved venue legitimately exposes a model, Vector Cannon should make that route repeatable without making the operator rediscover the whole firing procedure.

Vector Cannon does **not** bypass authentication, access controls, rate limits, provider restrictions, or terms.

## 3. Repository shot

The original read-only repository cannon remains available:

```bash
vector-cannon fire \
  --provider unorouter \
  --model gpt-oss-120b:free \
  --repo ../your-repo \
  --prompt prompts/vector_cannon/repo_recon.md \
  --max-usd 0.25 \
  --tag free-recon-01
```

Repository shots expose only bounded read-only tools. Fired models can list paths, read bounded text files, and search text. They do not receive repository write, commit, PR, deploy, shell, or arbitrary network tools.

## 4. Personal Vector Ledger

Every direct or repository shot writes a JSON firing record under `~/.vector-cannon/runs/` by default.

Summarize the current firing history:

```bash
vector-cannon ledger
```

The first ledger view aggregates:

- shot count
- observed/estimated spend
- average elapsed time
- failures / truncated runs
- input/output tokens
- provider
- exact model id

Raw run JSON remains the source of truth. Failures are preserved because failed shots are evidence for future routing.

## Why

Frontier models can be excellent and expensive. Smaller models, free tiers, promotional pricing, routers, and new providers change constantly.

Vector Cannon treats inference capacity like ammunition:

1. discover an approved firing lane,
2. fire the cheapest plausible model,
3. measure result, latency, and cost,
4. escalate only when a material question remains,
5. retain the shot as evidence for the next mission.

The long-term question is not simply **"Which AI is smartest?"**

It is:

> **Who should I fire, when should I fire them, where should I fire them from, how many shots should I take, and how much should I spend?**

## VECTOR CANNON MODE — target architecture

The maximum-form product is a **Personal AI Fire-Control System**.

Future firing sequence:

1. **MISSION LOAD** — load the target and similar historical shots
2. **ENERGY LINE — ALL CONNECTED** — enumerate available inference venues/models
3. **LANDING GEAR / EISEN LOCK** — lock budget, permissions, data boundary and shot count
4. **CHAMBER PRESSURE CHECK** — confirm price, availability and observable balance/quota
5. **RIFLING ROTATION START** — derive a firing plan from the personal ledger
6. **VECTOR ALIGNMENT** — assign recon / critique / coding / synthesis roles
7. **READY TO FIRE**
8. **FIRE**

Every readiness message should correspond to a real system state. No fake telemetry.

## Personal firing doctrine

As the ledger grows, Vector Cannon should learn that different operators need different weapons at different times.

One operator may evolve toward cheap high-volume swarm shots. Another may favor critique-heavy routes. Another may use frontier models only as a final sniper shot.

The system should eventually be able to say things such as:

- this task does not justify a frontier shot yet,
- this class of task repeatedly fails on the cheap route,
- this model is under-tested for your workload,
- cheap recon followed by one frontier synthesis shot has historically been the best tradeoff,
- you are escalating too late or using an expensive model as overkill.

That makes Vector Cannon both a firing controller and an AI-use training system.

## Providers

Current built-in approved lanes:

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

## Budget semantics

`--max-usd` is a best-effort per-shot ceiling.

Vector Cannon fails closed when it cannot establish a trusted price. Depending on the provider, cost may come from an exact balance delta, provider-reported request cost, live catalog pricing, a provider-declared free model, or explicit current token prices supplied by the operator.

For providers without trusted live pricing, pass current prices explicitly:

```bash
vector-cannon shot \
  --provider openai \
  --model YOUR_MODEL_ID \
  --text 'Your mission here' \
  --input-price-per-million CURRENT_INPUT_PRICE \
  --output-price-per-million CURRENT_OUTPUT_PRICE \
  --max-usd 1.00
```

Budget control is intended to prevent careless burns, not to replace provider-side spend limits.

## Optional GitHub Issue FIRE channel

The included `.github/workflows/vector-cannon.yml` can fire only when the repository owner opens an issue whose title starts with `[VECTOR-CANNON]`.

Store provider keys as GitHub Actions repository secrets. The workflow accepts provider/model/budget fields from the issue body, but never API keys or arbitrary provider URLs.

## Safety boundary

- approved provider hosts are fixed in code
- credentials are read from dedicated environment variables
- arbitrary provider URLs are not accepted from prompts/issues
- repository mode is read-only and filters common secrets/build/dependency paths
- budget checks fail closed when pricing cannot be trusted

Treat repository filtering as a defensive boundary, not a formal DLP system. Do not aim Vector Cannon at sensitive data unless the selected provider and workflow are appropriate for that data.

## Status

v0.2 development line: **Inference Access Layer + Personal Vector Ledger**.

The MVP is intentionally smaller than the vision.

**Maximum form:** Personal AI Fire-Control System.

**First implementation:** make legitimate inference dramatically easier to fire, then turn every firing record into better future routing.

See issue #1 for the roadmap.

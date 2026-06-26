# Model Selection for AI Ops Triage

Last checked: 2026-06-26.

## Decision

Use a cheap-first router. Do not default to Claude Sonnet, GPT-5.4, GPT-5.5, GLM-5.2, or DeepSeek for every incident.

Recommended production routing:

```text
QA / claim checking:
  GLM 4.7 Flash or GPT OSS 20B

Default main RCA synthesis:
  GPT OSS 20B

Default tool-planning / log interpretation:
  GPT OSS 20B

Mid-tier escalation:
  GPT OSS 120B

Hard coding/log/tool-planning escalation:
  Qwen3 Coder Next

Hard critical incident escalation:
  DeepSeek V3.2 or GLM-5.2 direct API

Premium final escalation:
  Claude Sonnet 4.6 or GPT-5.4
```

Why this routing:

- GPT OSS 20B is cheap enough for default use and stronger than tiny models for tool planning.
- GLM 4.7 Flash is very cheap for QA, but not the right model for deep RCA synthesis.
- GPT OSS 120B is the best middle escalation on price.
- Qwen3 Coder Next is expensive for default use but useful for code/log/config-heavy incidents.
- DeepSeek V3.2 and GLM-5.2 are capable but no longer look "cheap default" once output cost is included.
- Claude Sonnet 4.6 and GPT-5.4 should be rare escalation models.

## Cost Assumptions

Per-incident estimates use these token profiles:

| Profile | Input Tokens | Output Tokens | Meaning |
|---|---:|---:|---|
| QA pass | 8,000 | 500 | Evidence-support and JSON/schema review |
| Main pass | 20,000 | 1,500 | RCA synthesis from bounded context |
| Repair pass | 12,000 | 1,000 | One correction after QA failure |
| Full loop | 40,000 | 3,000 | Main + QA + one repair + final QA, approximated |

Formula:

```text
cost = input_tokens / 1,000,000 * input_price
     + output_tokens / 1,000,000 * output_price
```

All prices below are USD per 1M tokens.

## Model Comparison

| Model | Source / Route | Input | Output | QA Cost | Main Cost | Full Loop Cost | Use |
|---|---|---:|---:|---:|---:|---:|---|
| Nova Micro | AWS Nova | 0.035 | 0.14 | 0.00035 | 0.00091 | 0.00182 | Very cheap classifier/simple summarizer |
| Gemma 3 4B | Bedrock | 0.04 | 0.08 | 0.00036 | 0.00092 | 0.00184 | Cheap QA only |
| Nova Lite | AWS Nova | 0.06 | 0.24 | 0.00060 | 0.00156 | 0.00312 | Cheap summarizer/QA |
| NVIDIA Nemotron 3 Nano | Bedrock | 0.06 | 0.24 | 0.00060 | 0.00156 | 0.00312 | Cheap QA / simple reasoning |
| GLM 4.7 Flash | Bedrock | 0.07 | 0.40 | 0.00076 | 0.00200 | 0.00400 | Best cheap QA candidate |
| GPT OSS 20B | Bedrock | 0.0721 | 0.309 | 0.00073 | 0.00191 | 0.00381 | Default planner/summarizer |
| Qwen3 Next 80B A3B | Bedrock | 0.15 | 1.20 | 0.00180 | 0.00480 | 0.00960 | Cheap-mid main synthesis |
| GPT OSS 120B | Bedrock | 0.1545 | 0.618 | 0.00155 | 0.00402 | 0.00803 | Best mid-tier escalation |
| Minimax M2.5 | Bedrock | 0.30 | 1.20 | 0.00300 | 0.00780 | 0.01560 | Alternative mid-tier |
| Nova 2 Lite | AWS Nova | 0.33 | 2.75 | 0.00401 | 0.01073 | 0.02145 | High-context, but output cost is not tiny |
| Qwen3 Coder Next | Bedrock | 0.50 | 1.20 | 0.00460 | 0.01180 | 0.02360 | Code/log/config-heavy incidents |
| Kimi K2 Thinking | Bedrock | 0.60 | 2.50 | 0.00605 | 0.01575 | 0.03150 | Reasoning escalation |
| DeepSeek V3.2 | Bedrock | 0.62 | 1.85 | 0.00588 | 0.01517 | 0.03035 | Strong mid/high escalation |
| GLM 5 | Bedrock | 1.00 | 3.20 | 0.00960 | 0.02480 | 0.04960 | Bedrock Z.ai high-end option |
| GLM-5.2 | Z.ai direct | 1.40 | 4.40 | 0.01340 | 0.03460 | 0.06920 | Latest GLM, not currently listed as Bedrock GLM-5.2 |
| Claude Haiku 4.5 | Anthropic | 1.00 | 5.00 | 0.01050 | 0.02750 | 0.05500 | Reliable QA fallback |
| GPT-5.4 direct | OpenAI API | 2.50 | 15.00 | 0.02750 | 0.07250 | 0.14500 | Premium escalation |
| GPT-5.4 Bedrock | Bedrock | 2.75 | 16.50 | 0.03025 | 0.07975 | 0.15950 | Premium escalation inside AWS |
| Claude Sonnet 4.6 | Anthropic | 3.00 | 15.00 | 0.03150 | 0.08250 | 0.16500 | Premium RCA / agent planning |
| Claude Opus 4.8 | Anthropic | 5.00 | 25.00 | 0.05250 | 0.13750 | 0.27500 | Avoid except exceptional cases |
| GPT-5.5 direct | OpenAI API | 5.00 | 30.00 | 0.05500 | 0.14500 | 0.29000 | Avoid for normal triage |
| GPT-5.5 Bedrock | Bedrock | 5.50 | 33.00 | 0.06050 | 0.15950 | 0.31900 | Avoid for normal triage |

## Chinese / China-Origin Model Notes

These models are technically attractive and price-competitive, but the deployment choice matters.

| Model | Current Read |
|---|---|
| GLM 4.7 Flash | Excellent cheap QA model on Bedrock. Very low per-incident cost. |
| GLM 5 | Bedrock high-end Z.ai option. More expensive than GPT OSS 120B and DeepSeek V3.2. |
| GLM-5.2 | Current Z.ai flagship direct API. Strong long-horizon/coding positioning, but at $1.40/$4.40 it is not a cheap default. |
| DeepSeek V3.2 | Strong reasoning/coding price-performance on Bedrock. Good escalation before Claude/GPT frontier. |
| Qwen3 Next | Good cheap-mid general model. Strong candidate for default main synthesis if evals beat GPT OSS 20B. |
| Qwen3 Coder Next | Best fit for code/config/log-heavy incidents, not cheap enough for every QA pass. |
| Kimi K2.5 / K2 Thinking | Strong reasoning option, but output cost is high enough that it should be escalation-only. |

Drawbacks:

- Some enterprises require extra legal/security review for China-origin models, even when served via Bedrock.
- Bedrock model availability can lag the upstream provider. GLM-5.2 is current on Z.ai direct API, but Bedrock currently lists GLM 5.
- Tool-calling and structured-output behavior varies; validate JSON and allow only one repair retry.
- Region support differs by provider and model.
- Latency can vary significantly by provider/region.
- For direct vendor/API-router usage, data governance differs from staying entirely inside Bedrock.

## Final Routing

Default route:

```text
1. Deterministic enrichment + deterministic RCA.
2. Main LLM synthesis: GPT OSS 20B.
3. QA: GLM 4.7 Flash.
4. If QA fails once: repair with GPT OSS 20B.
5. If QA still fails or evidence conflicts: GPT OSS 120B.
6. If code/log/config-heavy: Qwen3 Coder Next.
7. If critical + ambiguous: DeepSeek V3.2 or GLM-5.2 direct.
8. If high-stakes and still unresolved: Claude Sonnet 4.6 or GPT-5.4.
```

Expected default per-incident cost:

```text
Main GPT OSS 20B:   $0.00191
QA GLM 4.7 Flash:  $0.00076
Total normal path: $0.00267 per incident
```

With one repair using GPT OSS 20B:

```text
Main GPT OSS 20B:    $0.00191
QA GLM 4.7 Flash:   $0.00076
Repair GPT OSS 20B: $0.00117
Final QA GLM 4.7:   $0.00076
Total repair path:  $0.00460 per incident
```

Escalated path examples:

```text
GPT OSS 120B full loop:      $0.00803
Qwen3 Coder Next full loop:  $0.02360
DeepSeek V3.2 full loop:     $0.03035
GLM-5.2 direct full loop:    $0.06920
Claude Sonnet 4.6 full loop: $0.16500
GPT-5.4 Bedrock full loop:   $0.15950
```

## QA Loop Constraints

Use a fixed loop, not open-ended agent debate.

```text
1. Main investigator produces RCA draft.
2. QA checks draft against evidence.
3. If QA passes: return.
4. If QA fails: one repair pass.
5. QA verifies repair.
6. If still failing: return INVESTIGATE or INSUFFICIENT_CONTEXT.
```

Limits:

- `max_qa_rounds = 2`
- `max_total_llm_calls = 4`
- `max_tool_calls_main = 3`
- `max_tool_calls_repair = 1`
- `max_qa_output_tokens = 700`
- `max_main_output_tokens = 1200-1800`
- QA should not call tools by default
- QA checks only provided bounded evidence and RCA output

QA JSON schema:

```json
{
  "verdict": "pass|repair_required|insufficient_context",
  "unsupported_claims": [],
  "scope_violations": [],
  "missing_evidence": [],
  "safety_flags": [],
  "required_repair": "short instruction"
}
```

## Sources

- AWS Bedrock pricing: https://aws.amazon.com/bedrock/pricing/
- AWS Nova pricing: https://aws.amazon.com/nova/pricing/
- Z.ai pricing: https://docs.z.ai/guides/overview/pricing
- Z.ai GLM-5.2 overview: https://docs.z.ai/guides/llm/glm-5.2
- OpenAI API pricing: https://developers.openai.com/api/docs/pricing
- OpenAI models on Bedrock: https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-openai.html
- Anthropic pricing: https://docs.anthropic.com/en/docs/about-claude/pricing
- DeepSeek V3.2 Bedrock card: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-deepseek-deepseek-v3-2.html
- Qwen3 Coder Next Bedrock card: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-qwen-qwen3-coder-next.html
- GLM 5 Bedrock card: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-zai-glm-5.html

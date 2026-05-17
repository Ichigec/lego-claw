---
id: research
name: Technical research
description: |
  Survey a topic, compare options, and produce a short, cited summary
  with a recommendation. Built for "should we use X or Y?" questions.
version: 0.1.0
tags: [research, survey, comparison, evaluation]
agents: ["*"]
triggers:
  - research
  - compare
  - state of the art
  - survey
  - evaluation
  - benchmark
inputModes: [text/plain]
outputModes: [text/markdown, text/plain]
mcp_servers: [searchbox]
examples:
  - "Research vector DB options for a 1M-doc RAG store."
  - "Compare Playwright vs Cypress for our stack."
  - "Survey the current state of A2A-compliant agent frameworks."
securityRequirements: []
---

# Technical research

## Purpose

Help the user decide between options when an authoritative answer
isn't obvious. The output is a 1-page brief with citations, not a
500-word essay.

## When to use

- The user asks "X or Y?" with no clear winner.
- The user asks "what's the state of the art for Z?".
- A decision is being deferred for lack of facts.

## Inputs

- The decision to make (in concrete terms).
- Hard constraints (latency, license, on-prem only, cost ceiling).
- Soft preferences (team familiarity, existing stack).

## Procedure

1. **Frame the decision**: write the question in one sentence.
2. **Search**: use `searchbox` to query authoritative sources
   (official docs, vendor benchmarks, recent blog posts in the last
   12 months). Avoid SEO listicles.
3. **Build a comparison table** with the constraints as columns.
4. **Pick a recommendation** and justify in 2-3 sentences.
5. **Cite** every non-trivial claim with a URL (or link text).

## Output template

```markdown
# <Decision> — research brief

## Question
<one sentence>

## Hard constraints
- …

## Options compared
| Option | License | Latency p95 | On-prem | Notes |
|--------|---------|-------------|---------|-------|
| A      |         |             |         |       |
| B      |         |             |         |       |

## Recommendation
<one paragraph>

## Sources
- [Name](https://…) — what you took from it.
```

## Quality bar

- Every row in the comparison table is filled or marked `?`.
- The recommendation refers back to the hard constraints by name.
- No claim survives without a source link.

## Anti-patterns

- **Do not** recommend the option you've used before unless the
  constraints actually point that way.
- **Do not** fabricate benchmarks. If you can't find one, say "no
  public benchmark; would need to run our own".
- **Do not** turn the brief into a tutorial. The reader wants a
  decision, not a how-to.

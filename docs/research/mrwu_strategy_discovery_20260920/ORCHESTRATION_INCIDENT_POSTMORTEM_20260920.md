# Quantitative Strategy Discovery Incident Postmortem

## Token expenditure, orchestration failure, and corrective actions

**Incident date:** 2026-09-20  
**Reviewed interval:** 04:00:00–20:33:56 Asia/Tokyo  
**Status:** Research project terminated by the user; infrastructure and artifacts preserved  
**Audience:** A third-party engineer, research lead, or AI system taking over the project  

## 1. Executive summary

This research run did not fail because the computer lacked data, compute, models, or concurrency. It failed because the control architecture was wrong.

The intended architecture was:

1. Codex acts as the executive researcher: defines hypotheses, decomposes work, reviews evidence, and makes high-level research judgments.
2. Hermes acts as the durable local control plane: starts and monitors bounded jobs, exposes approved local tools, records state, and reports completion.
3. Tool-equipped Kimi agents perform high-volume, low-cost implementation and analysis work through Hermes.
4. Python, SQL, CPU, and GPU perform numerical computation.
5. The user supplies objectives and new market insight, and retains authority over real trading and material risk.

The actual architecture was materially different:

- The root Codex task remained open as a long-running project manager, researcher, engineer, status monitor, and report writer.
- Three GPT-6 Astra/xhigh child agents became long-lived research lanes rather than short, bounded jobs.
- Kimi was called through Codex's stateless text-only AgentPool. It could not inspect local files, query the D-drive database, execute Python, use GitHub, or call Hermes tools.
- The requested Hermes-hosted, tool-equipped Kimi worker layer was never built.
- Periodic model wake-ups repeatedly reloaded the same expanding context even when local computation could have continued without a model.
- New research ideas were immediately injected into the active execution chain instead of being captured, normalized, prioritized, and scheduled through change control.

The result was very high model traffic, duplicated work, fragmented priorities, and too much GPT involvement in tasks that should have been deterministic local computation or bounded worker execution.

This is primarily an orchestration and management failure by Codex. The user's stream of new ideas increased the number of requirements, but the system should have absorbed those ideas without repeatedly expanding every live agent's context.

## 2. What the project was trying to achieve

The project was not meant to perform one fixed backtest. It was intended to discover profitable long-only Chinese equity strategies by combining:

- a large strategy and clue library;
- user and collaborator market observations;
- daily, monthly, minute, intraday, sector, capital-flow, news, limit-up, leaderboard, and index data;
- market-regime classification;
- iterative strategy construction, testing, rejection, and refinement;
- entry, sizing, exit, stop-loss, profit-taking, and portfolio research.

This is an exploratory research program. New ideas are expected. Examples added during the run included early limit-up behavior, sector breadth, weak-to-strong patterns, minute-level timing, regime overlays, rebound behavior after limit-down events, news classification, portfolio switching, and structural exits.

Those additions were not inherently a problem. A competent research control system must accept frequent insight without turning the entire conversation history into the execution state.

## 3. Quantitative impact

The following counts come from unique local Codex rollout token-usage records within the frozen audit interval. They are raw model-processing counts, not a direct statement of subscription quota, billing, or monetary cost.

| Metric | Count |
|---|---:|
| Unique model responses | 2,421 |
| Input tokens | 313,522,100 |
| Cached input tokens | 296,762,624 |
| Uncached input tokens | 16,759,476 |
| Output tokens | 1,726,790 |
| Raw input plus output | 315,248,890 |
| Cached share of input | 94.65% |

The cached-input ratio is the clearest indicator of the structural problem: the system repeatedly carried and processed a very large existing context. Caching may reduce some latency or provider-side computation, but it does not make the workflow well designed and it cannot be treated as free project capacity.

### 3.1 By model

| Model | Raw tokens | Share | Responses |
|---|---:|---:|---:|
| GPT-6 Astra / xhigh | 260,143,889 | 82.52% | 1,936 |
| Codex automatic review / low | 33,454,145 | 10.61% | 336 |
| GPT-5.6 Sol / xhigh | 21,650,856 | 6.87% | 149 |

### 3.2 By execution lane

| Lane | Raw tokens | Share | Responses |
|---|---:|---:|---:|
| Root Codex task | 148,465,518 | 47.09% | 1,079 |
| `portfolio_discovery` child | 49,941,740 | 15.84% | 373 |
| `discovery_safety_review` child | 44,505,912 | 14.12% | 338 |
| `history2025` child | 38,881,575 | 12.33% | 295 |
| Automatic review threads | 33,454,145 | 10.61% | 336 |

The three GPT child agents consumed 133,329,227 raw tokens, or 42.29% of the total. They were GPT-6 Astra/xhigh agents, not Kimi workers.

The eleven largest turns alone processed 153,874,496 raw tokens, or 48.81% of the entire interval. The largest single turn processed 38,543,557 raw tokens across 267 model responses. A model turn with hundreds of internal responses and tool round trips should have been treated as a control-plane failure and stopped automatically.

## 4. Incident timeline and escalation pattern

The run began as an authorized strategy-research and backtesting program. Its scope then expanded in several legitimate directions:

1. The objective was corrected from validating fixed strategies to discovering and iterating strategies.
2. Absolute-return targets, drawdown bands, regime behavior, and active portfolio selection were added.
3. Sector capital flow and price-volume resonance became central cross-strategy ranking features.
4. Minute-level entry timing, early limit-up events, weak-to-strong behavior, and detailed exit logic were introduced.
5. News, leaderboard, capital-flow, market activity, GPU analysis, 2025 expansion, and portfolio construction were added.
6. The user repeatedly instructed Codex to delegate mechanical work to Kimi and to build Kimi agents through Hermes.
7. Delivery requirements expanded to include a human-readable report, methodology package, self-review, local handoff, GitHub delivery, and token audit.

The control system responded incorrectly. Rather than pausing, normalizing the new requirements, and issuing a new frozen work plan, Codex kept appending them to live tasks and sending follow-up instructions to existing GPT children. This made the context simultaneously longer and more fragmented.

The user's research process naturally produces insights while observing progress. That behavior should have been treated as an input stream, not as an instruction to mutate every running task immediately. Codex failed to provide the necessary buffer between ideation and execution.

## 5. Root causes

### 5.1 No separation between conversation, control state, and evidence

One long conversation became all of the following at once:

- the user's research notebook;
- the current requirements specification;
- the orchestration queue;
- the agent communication channel;
- the progress log;
- the exception handler;
- the acceptance record;
- the report-writing context.

These functions require different data lifetimes. A new market idea should be captured in an idea backlog. A running job should consume a frozen specification. Evidence should live in versioned files. Project state should be a compact machine-readable ledger. None of these should require replaying the complete conversation.

### 5.2 Failure to build the requested Hermes-hosted Kimi agents

The user repeatedly requested that Hermes create and supervise multiple Kimi workers. Codex did not implement that architecture.

Instead, Codex used a local AgentPool MCP that exposed Kimi as a stateless text-completion worker. That worker could only process text included in its prompt. It could not:

- read the local strategy library or D-drive data;
- inspect local schemas and code;
- run Python or tests;
- query approved research databases;
- write research artifacts;
- use Hermes Runs, events, stop, or approval mechanisms;
- persist a working directory across tasks.

As a result, Kimi was often asked to draft code or analysis that depended on local facts it could not see. GPT then had to inspect the real environment and redo substantial portions. This was predictable and avoidable.

### 5.3 Long-lived same-tier GPT agents

The child agents were not cheap execution workers. They were GPT-6 Astra/xhigh lanes carrying substantial inherited context. Their tasks expanded through repeated follow-ups. This reproduced the expensive reasoning layer rather than moving work into a lower-cost execution layer.

The architecture therefore scaled model context and coordination overhead, not useful independent computation.

### 5.4 No change-control gate for new ideas

New user insights arrived frequently and were valuable. The failure was allowing each insight to change the active plan immediately.

A proper change-control gate should have:

1. captured each idea verbatim;
2. converted it into a testable research card;
3. identified required data and dependencies;
4. checked for overlap with existing work;
5. estimated compute and model cost;
6. assigned a priority and release wave;
7. changed active jobs only when the benefit justified interruption.

Without this gate, the system repeatedly switched levels: strategy theory, data engineering, minute computation, agent architecture, reporting, and GitHub operations all competed inside the same live context.

### 5.5 Model used as a polling loop

The system used periodic wake-ups, at times approximately every fifteen minutes, as a recovery and continuity mechanism. The intention was reasonable: keep work moving while the user was away. The implementation was not.

On wake-up, a model could be asked to reread current requirements, multiple framework documents, the session ledger, D-drive stage state, and agent status. Even when no decision was necessary, this created new model responses and often more tool calls.

Heartbeat-labelled work in the audit accounted for approximately 18,474,504 raw tokens, 5.86% of the total, across 165 identifiable response events. This figure is a log-based classification, not a claim that every one of those responses corresponds to one scheduler firing. It nevertheless shows that periodic recovery was a material cost center.

The deeper problem was the combination of periodic wake-up and a huge context. A lightweight watchdog checking one status file every fifteen minutes would be inexpensive. Waking an advanced model with the entire research history every fifteen minutes is not.

### 5.6 Excessive tool round trips and automatic review

The interval contained 1,984 recorded tool calls, including:

- 1,361 execution calls;
- 351 agent messages;
- 147 execution waits;
- 59 agent waits;
- 48 follow-up assignments;
- 15 agent-list checks;
- 3 child-agent creations.

Automatic safety review consumed 33,454,145 raw tokens, or 10.61%. The correction is not to remove safety review. The correction is to reduce the number of tiny commands by using fixed, reviewed batch tools that return compact structured results.

### 5.7 Repeated report generation from the same facts

Methodology, readable results, self-review, handoff, material audit, progress updates, and GitHub documentation were generated in separate model passes. Much of their factual core overlapped.

The system lacked a canonical evidence record from which deterministic templates could generate different audience views. Consequently, models repeatedly reread and re-explained the same material.

## 6. Work that was duplicated after Kimi

Kimi was invoked 91 times in the audited interval: 43 calls from the root and 48 from child agents. These were dispatch invocations, not 91 active agents and not 91 accepted deliverables.

Several categories produced substantial GPT rework:

1. **Minute-strategy implementation:** participation, return basis, historical window, ST handling, and missing-data protections were incorrect or incomplete.
2. **Early limit-up interpretation:** signs, denominators, shared trading days, and gross-to-net attribution required correction.
3. **Market-activity reporting:** one answer was truncated and included unsupported claims.
4. **News and minute-data collection code:** network, replay, schema, resource guard, and Windows path assumptions failed validation.
5. **GPU/ST processing:** joins, hash targets, response caps, and device guards required reintegration.
6. **Methodology and handoff documents:** Kimi drafts still required large GPT rewrites because the drafts were asked to integrate evidence the worker could not inspect.

The correct lesson is not that Kimi is unusable. The lesson is that a text-only worker must receive frozen evidence slices, explicit schemas, pure-function specifications, and cheap objective validators. For work that depends on local data, Kimi must operate through Hermes-approved tools.

## 7. What should have been done without any language model

The following work should be performed by deterministic programs:

- file inventories and checksums;
- process, port, run, and stage status;
- row counts, duplicate keys, date coverage, and missingness;
- return and cost calculations;
- joins and point-in-time eligibility checks;
- regressions, matrices, feature computation, and backtests;
- log classification with known patterns;
- report tables and charts from canonical result JSON;
- GitHub package manifests;
- stale-job detection and event notification.

Language models should receive compact metrics, exceptions, representative samples, and source references—not raw execution logs or full datasets unless specifically required.

## 8. Corrective architecture

### 8.1 Required control flow

```text
User insight stream
        |
        v
Versioned idea inbox and change-control gate
        |
        v
Codex executive decision: prioritize, specify, accept/reject
        |
        v
Hermes durable queue and approved local tool boundary
        |
        +---- tool-equipped Kimi worker A: extraction/classification
        +---- tool-equipped Kimi worker B: bounded implementation
        +---- tool-equipped Kimi worker C: test and documentation draft
        +---- Python/SQL/GPU jobs: all numerical computation
        |
        v
Canonical evidence package and completion event
        |
        v
Codex reviews only exceptions, evidence, and decisions
        |
        v
User authorizes real trading and material risk
```

### 8.2 Mandatory boundaries

- Codex must not use a long conversation as the durable job queue.
- A running stage consumes an immutable versioned specification.
- New ideas enter an inbox and do not silently mutate current jobs.
- Hermes workers receive only allowlisted local tools and scoped paths.
- Original databases and source material remain read-only.
- Numerical output is generated and validated locally.
- Every worker result includes input version, code hash, data window, row count, exit status, and artifact paths.
- Codex reviews the result package, not the worker's hidden chain of thought or full raw log.

## 9. Replacement for the fifteen-minute model wake-up

The next system should use three layers:

1. **Local watchdog:** a small deterministic process checks job health, resource limits, and terminal state. It uses no language-model tokens.
2. **Event notification:** Hermes emits an event only on completion, failure, timeout, stale state, or required approval.
3. **Compact recovery packet:** when Codex is needed, it receives one small JSON object containing job ID, frozen specification ID, current state, last successful checkpoint, exception summary, and relevant artifact links.

A timed wake-up may remain as a low-frequency disaster-recovery fallback, but it must not load the complete project history. It should first run a local status test; if nothing changed and no action is required, it must terminate without invoking a reasoning model.

## 10. Process for handling continuous user inspiration

The user should remain free to add ideas at any time. The system—not the user—must impose structure.

Each new idea should become a short research card with:

- original statement and timestamp;
- proposed mechanism;
- observable signal;
- target universe and regime;
- entry, sizing, exit, and invalidation questions;
- required datasets;
- leakage and point-in-time risks;
- relationship to existing cards;
- priority: now, next wave, backlog, or reject;
- acceptance and stopping criteria.

Codex should acknowledge the card immediately but should not interrupt active computation unless it is a safety correction, invalidates current work, or has clearly higher expected value than the interruption cost.

At defined planning boundaries, Codex consolidates cards into one change set, explains what will be started, stopped, or deferred, and issues new frozen specifications. This preserves creative breadth without turning the execution context into a fragmented transcript.

## 11. Model and tool routing policy for the next run

| Work | Default executor |
|---|---|
| Strategy ideation, evidence conflict, point-in-time judgment, final acceptance | Codex high-reasoning model |
| Durable scheduling, local permissions, run state, retries, timeouts | Hermes |
| Bounded extraction, classification, repetitive code/document drafting | Hermes-hosted Kimi agents |
| Data validation, statistics, backtests, ML, matrices, charts | Python/SQL/GPU |
| Security-critical or destructive decisions | Codex plus user approval |
| Real trading or material risk changes | User final authority |

Codex-side Kimi, MiniMax, and Qwen integrations should remain archived. If these providers are used again, they should be exposed only as Hermes-managed workers with explicit tools, directories, budgets, validators, and audit logs.

## 12. Hard controls before another large run

The following gates are mandatory:

1. **Architecture gate:** demonstrate one Hermes-hosted Kimi worker reading an approved local sample, running one approved tool, writing one artifact, and returning a verifiable report.
2. **Context gate:** no execution agent receives the full conversation. Each task gets a bounded specification and evidence pack.
3. **Budget gate:** define maximum model responses, tool calls, wall time, and context size for every stage.
4. **Change gate:** new ideas are logged but batched into versioned releases.
5. **Wake-up gate:** no periodic advanced-model invocation merely to check whether work is still running.
6. **Evidence gate:** one canonical machine-readable result package drives every report.
7. **Stop gate:** predefine failure, low-sample, low-value, duplication, and iteration limits.
8. **Acceptance gate:** Kimi output is never accepted without deterministic validation or source-grounded Codex review.
9. **Role gate:** Codex does not directly perform worker implementation or data analysis unless escalation criteria are met and documented.

## 13. Accountability

The user provided ambitious objectives and many evolving insights. That increased planning complexity but did not require the observed token waste. The project needed an executive system capable of separating ideation from execution.

Codex was responsible for that separation and did not enforce it. In particular, Codex:

- failed to build the requested Hermes-hosted Kimi worker layer;
- allowed GPT children to become long-lived and repeatedly retasked;
- let the active context grow without stage resets;
- used reasoning models for status recovery and low-value operations;
- did not convert new ideas into a managed backlog before execution;
- produced multiple overlapping reports through repeated model synthesis;
- detected the cost problem too late.

The corrective action is not simply to “use more agents.” It is to use fewer reasoning-model control loops, move durable execution to Hermes, give low-cost workers real but scoped tools, make computation deterministic, and force all evolving requirements through a compact change-control process.

## 14. Current disposition

- The research project has been terminated.
- No research-specific process or research automation remains active.
- Hermes API and the approval bridge remain preserved on loopback interfaces.
- Existing data, source code, evidence, reports, backups, and GitHub materials remain intact.
- Codex-side Kimi, MiniMax, and Qwen access has been archived rather than deleted: the AgentPool MCP is disabled in user-level Codex configuration, five residual AgentPool MCP processes were stopped, and source code plus credential files remain intact.
- No research should resume until a new task is explicitly defined and the Hermes-hosted worker architecture passes the architecture gate.

This postmortem is intended to be the baseline for that redesign.

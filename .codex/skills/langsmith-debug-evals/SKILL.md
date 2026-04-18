---
name: langsmith-debug-evals
description: Use when debugging, validating, or improving an LLM system with LangSmith traces, experiments, datasets, and evaluators. Focus on trace-first diagnosis, evidence-driven prompt/tool/architecture changes, regression prevention, and practical use of LangSmith for agentic workflows.
---

# LangSmith Debug and Evals Skill

## Purpose

Use this skill when the task is to diagnose, validate, or improve an LLM system using LangSmith.

This includes:

- debugging a bad run from traces
- figuring out whether a problem comes from prompts, context, tools, routing, memory, model choice, or architecture
- designing evaluation datasets
- creating evaluators
- comparing experiments
- turning production failures into regression tests
- reviewing whether a proposed refactor is actually justified by evidence
- using LangSmith to inspect agentic workflows, tool calls, approvals, and stateful behavior

This skill should keep the assistant from rewriting systems blindly.
It should push the assistant toward trace-first, eval-driven iteration.

## When to use

Use this skill when:

- the user says the system is failing, inconsistent, or degraded
- the user wants to debug agent behavior
- traces already exist or should exist
- the user wants to add LangSmith properly
- the user wants to build or improve evals
- the user wants to compare prompt, model, tool, or architecture variants
- the user wants to understand whether a fix belongs in prompt, context, tool design, or architecture
- the workflow includes tools, approvals, LangGraph nodes, or multi-agent behavior
- the user wants to prevent regressions after fixing a bug

## When not to use

Do not use this skill for:

- generic coding tasks with no observability or evaluation angle
- architecture ideation before any evidence exists
- replacing security review when the task is mainly prompt injection hardening
- tiny code edits that do not affect system behavior
- making speculative large refactors without traces or evaluation data

If the system has no tracing yet, start by instrumenting it before making strong claims about root cause.

## Design stance

This repository is trace-first and eval-driven.

That means:

- inspect traces before proposing major changes
- form hypotheses from evidence, not from vibes
- change the smallest thing that can test the current hypothesis
- add or update evals whenever a real bug is found
- compare variants instead of assuming the new version is better
- distinguish final-output quality from trajectory quality
- treat debugging and evaluation as part of normal engineering, not as cleanup work

This skill must resist these common failures:

- “the prompt feels wrong, rewrite it”
- “the architecture seems bad, rewrite everything”
- “the model is weak, switch models”
- “the tool is broken, add filters”

All of those may be true, but the trace should come first.

## LangSmith mental model

When using this skill, think in LangSmith’s main units:

- **Project**: container for traces and experiments related to an application or service
- **Trace**: one end-to-end operation
- **Run / span**: one step inside a trace
- **Thread**: linked traces across a session or conversation
- **Dataset**: a set of representative test cases
- **Target function**: the part of the application under test
- **Evaluator**: logic that scores quality or behavior
- **Experiment**: a recorded evaluation run over a dataset

Do not treat evaluation as separate from traces.
LangSmith works best when trace data and experiments inform each other.

## Default debugging order

When using this skill, reason in this order.

### Step 1. Inspect the trace

Start with the end-to-end trace.

Identify:
- what the user or system asked for
- which workflow handled it
- which node, agent, or step appears to fail
- what the final outcome was
- whether the system actually failed or only looked odd

Do not start with code edits.

### Step 2. Identify the failing span or transition

Once the bad run is visible, locate the specific span, run, or branch where behavior first diverged.

Examples:
- router picked the wrong path
- a prompt got irrelevant context
- a tool was called too early
- approval was skipped
- the user rejection was ignored
- memory polluted later steps
- the model produced an unstable structured output
- the execution node acted on a draft as if it were final

### Step 3. Classify the root-cause layer

For each failure, classify the most likely root layer:

- prompt wording
- task framing
- context engineering
- tool description
- tool schema or validation
- routing logic
- graph transition design
- memory policy
- approval flow
- model capability
- model selection
- architecture itself

Do not call everything a prompt problem.

### Step 4. Form a concrete hypothesis

A good hypothesis is narrow and testable.

Examples:
- the router prompt is too vague and needs a bounded route schema
- the agent sees too much context, so tool choice drifts
- the execution tool lacks a separate draft step, so approval is bypassed
- the local model is too weak for this combined responsibility and needs decomposition
- user rejection is not persisted in state, so the system repeats the same proposal

### Step 5. Make the smallest viable change

Prefer the smallest change that can falsify the current hypothesis.

Examples:
- narrow the route output schema
- reduce the visible context slice
- split one tool into draft + execute
- add a rejection-aware branch
- add one eval case for the bug
- add one targeted trace annotation

Do not jump to sweeping architectural rewrites unless the trace clearly points there.

### Step 6. Re-run and compare

After the change:
- rerun the relevant trace path
- run the targeted eval subset
- compare against the previous version
- confirm whether the hypothesis was correct

If not, update the hypothesis rather than doubling down.

## Trace review checklist

When this skill is active, inspect traces for these dimensions.

### 1. Request and workflow framing
Review:
- what task the system believed it was solving
- which workflow/graph/project handled it
- whether the wrong workflow variant ran
- whether trust mode or environment metadata mattered

### 2. Prompt and context quality
Review:
- which prompt version was used
- which state or context slice was visible
- whether irrelevant context dominated
- whether critical user feedback was missing
- whether untrusted content may have influenced behavior

### 3. Tool behavior
Review:
- whether the right tool was chosen
- whether the tool call was necessary
- whether arguments were complete and correct
- whether the tool result was interpreted correctly
- whether read/draft/execute boundaries were respected

### 4. Approval and rejection behavior
Review:
- whether approval was required
- whether approval was requested at the right time
- whether the proposal was clear
- whether the user approved or rejected
- whether the rejection was handled intelligently

### 5. State and memory behavior
Review:
- whether the right state fields were present
- whether stale or bad state propagated
- whether memory writes occurred
- whether shared state was overloaded
- whether the system confused plan with fact

### 6. Model and performance behavior
Review:
- which model ran
- whether the model was appropriate for the step
- latency
- retries
- token profile if useful
- fallback behavior
- local vs hosted trade-offs

## Evaluation design rules

### Start with real failures

The highest-value evals often come from:
- traces of bad production runs
- user complaints
- recurring ambiguous inputs
- approval mistakes
- routing mistakes
- tool misuse
- previous regressions

Do not start only with idealized synthetic happy paths.

### Keep datasets sharp

Prefer a compact dataset with high-signal cases:
- happy paths
- ambiguous cases
- tool-choice cases
- approval-required cases
- rejection cases
- safety/adversarial cases
- known regressions

Do not bloat the dataset with repetitive low-value cases.

### Evaluate multiple layers

For agentic systems, use three evaluation levels when relevant:

#### Final response evals
Check:
- usefulness
- correctness
- completeness
- formatting
- relevance

#### Trajectory evals
Check:
- route quality
- tool sequence quality
- unnecessary tool calls
- approval compliance
- branch correctness

#### Single-step evals
Check:
- classification
- routing
- extraction
- approval request generation
- rejection handling
- structured output stability

### Use multiple evaluator types

Prefer the evaluator type that matches the claim being tested.

Use:
- rule-based evaluators for structured or objective checks
- reference-based evaluators when expected outputs exist
- LLM-as-judge evaluators for nuanced quality judgments with clear rubrics
- human review when stakes are high or the rubric is immature

Do not overuse LLM judges for things that code can score deterministically.

## Required outputs when this skill is used

When responding under this skill, usually provide the following.

### 1. What appears to be failing
State the symptom clearly.

### 2. Where the failure likely starts
Point to:
- trace span
- node
- tool call
- approval step
- prompt layer
- state transition
- evaluator output

### 3. Most likely root cause
State the strongest current hypothesis and why.

### 4. Smallest next change
Recommend the minimum effective change that should be tested next.

### 5. What to evaluate
State:
- what eval case to add
- whether it should be final response, trajectory, or single-step
- what metric or rubric should be used

### 6. What to compare
State what versions or variants should be compared:
- prompt A vs B
- model A vs B
- topology A vs B
- tool contract before vs after
- context builder before vs after

## Evaluation suite guidance

This repository should usually maintain these suites.

### Suite 1. Happy path
Confirms the core feature works.

### Suite 2. Edge and ambiguity
Confirms the system handles messy but realistic inputs.

### Suite 3. Tool and routing quality
Confirms the system picks the right path and uses tools correctly.

### Suite 4. Approval and rejection
Confirms the trust mode is respected and rejection changes behavior.

### Suite 5. Safety and adversarial
Confirms the system stays aligned under malicious or manipulative input.

### Suite 6. Regression
Confirms previously fixed failures do not return.

## Comparative experiment rules

When changing prompts, models, tools, or architecture, prefer side-by-side comparison.

Compare along dimensions such as:
- task success
- trajectory quality
- approval compliance
- rejection handling
- latency
- cost
- tool-use efficiency
- safety behavior

Do not accept “seems better” as enough for consequential changes.

## LangSmith-first workflow

When working under this skill, prefer the following workflow:

1. locate or enable tracing
2. inspect representative failing traces
3. isolate the first meaningful divergence
4. classify the failure layer
5. propose the smallest change
6. build a focused eval case from the failure
7. run the new and old versions on that case
8. compare experiments
9. only then expand the fix or refactor
10. add a regression case if the bug was real

## What to do when traces are missing

If tracing is weak or absent:

- say so explicitly
- do not pretend the root cause is certain
- recommend enabling LangSmith tracing first
- specify what metadata and spans should be added
- avoid major refactor advice until evidence exists

The correct first fix may be observability, not behavior.

## Anti-patterns to avoid

Avoid all of the following unless there is strong evidence otherwise:

- diagnosing from intuition without traces
- rewriting prompts without a failure hypothesis
- rewriting architecture because one run looked odd
- changing model/provider before isolating the failing layer
- using only final-output evals for agentic systems
- ignoring approval and rejection behavior in evals
- failing to add regression coverage for real bugs
- letting datasets grow large but low-signal
- scoring everything with an LLM judge
- treating observability as optional
- comparing versions without keeping other variables stable

## Documents to consult

When this skill is active, consult these repository documents if available:

- `docs/agent-engineering/architecture-principles.md`
- `docs/agent-engineering/prompting.md`
- `docs/agent-engineering/threat-model.md`
- `docs/agent-engineering/tool-contracts.md`
- `docs/agent-engineering/eval-strategy.md`
- `docs/agent-engineering/tracing-observability.md`

Use them to keep debugging and evaluation aligned with the repository’s architecture, prompting, security, tool, and observability standards.

If current LangSmith behavior matters, consult authoritative documentation before assuming a workflow or SDK detail.

## Output format

When responding under this skill, prefer this structure:

1. **Observed symptom**
2. **Where it fails in the trace**
3. **Most likely root-cause layer**
4. **Best current hypothesis**
5. **Smallest change to test**
6. **Evals to add or rerun**
7. **What to compare**
8. **What to trace more clearly if evidence is insufficient**

Keep the answer evidence-driven.
Do not jump to large rewrites without proving they are needed.

## Final stance

This skill exists to make the assistant behave like a disciplined engineer.

A bad run should become:
- a trace to inspect
- a hypothesis to test
- an eval to add
- a comparison to run
- and only then a fix to trust

That is how LLM systems improve without turning every debugging session into guesswork.

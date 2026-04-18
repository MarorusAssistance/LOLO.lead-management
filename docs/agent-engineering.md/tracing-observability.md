# Tracing and Observability for LLM-First Agentic Systems

## Purpose

This document defines how tracing and observability should be designed and used in this repository.

The goal is not only to log that something happened. The goal is to make every meaningful run understandable enough that an engineer can inspect what the system saw, what it decided, what it executed, and why the outcome was good or bad.

In this repository, tracing is not optional instrumentation added at the end.
It is part of the architecture.

If a workflow cannot be inspected clearly, it is not mature enough to trust.

## Why observability matters here

This repository builds LLM-first systems with:

- semantic decision-making
- tool calling
- human approval modes
- multi-step workflows
- sometimes multi-agent orchestration
- stateful execution
- variable model quality depending on budget or deployment context

These systems fail differently from conventional software.

A failure may come from:

- poor prompt wording
- the wrong context being passed
- the wrong tool being selected
- a misleading retrieved document
- bad rejection handling
- a memory write that polluted future runs
- a graph transition that looked reasonable but was actually wrong
- a local model being too weak for a given step
- an expensive model being unnecessary for a given step

Without tracing, these failures collapse into vague impressions.
With tracing, they become diagnosable events.

## Core principles

### 1. Trace first, then speculate

When behavior is bad, inspect the trace before proposing a rewrite.

Do not begin by assuming:

- the prompt is the problem
- the model is the problem
- the architecture is the problem
- the tool is the problem

The trace should be the first source of evidence.

### 2. Observe the path, not just the result

A final answer may look acceptable even if the path was poor.

Observability in this repository must capture:

- what was requested
- what context was provided
- what the model saw
- what decision was made
- what tool was called
- what state changed
- what approval gate applied
- what the user approved or rejected
- what happened next

### 3. Trace semantics and control boundaries

This repository is LLM-first, so observability must make semantic decisions visible.

It is not enough to know that a function ran.
We also need to see:

- what prompt or instruction layer was active
- what task framing was used
- what tool policy applied
- which trust mode was active
- which agent or node owned the step
- what evidence influenced the decision

### 4. Traces must support both debugging and evaluation

A trace should be useful for:

- fixing a single bug
- understanding an entire workflow
- reviewing approval compliance
- diagnosing prompt injection influence
- comparing experiments
- deriving or validating evaluation datasets
- explaining why a refactor is justified

### 5. Good observability is selective, not noisy

Do not log everything blindly.

Capture enough detail to explain behavior, but organize it so that traces remain readable and useful.

The right answer is not maximal verbosity.
It is meaningful structure.

## Default observability model

This repository uses LangSmith as the primary tracing and observability layer.

Conceptually:

- a **trace** represents one end-to-end operation
- a **run/span** represents one unit of work inside that trace
- a **thread** groups multiple traces into one conversation or session when needed

That structure is important because most debugging should be able to move across all three levels:

- one failing span
- one broken trace
- one problematic user thread across turns

## What every trace should make visible

A representative trace in this repository should make it possible to answer:

1. What was the user or system trying to do?
2. Which workflow, graph, or agent handled it?
3. What trust mode was active?
4. What state or context was passed into each important step?
5. Which tools were available at that step?
6. Which tool was selected, if any, and why?
7. Whether approval was required and whether it was requested
8. Whether the user approved or rejected the action
9. What state changed after the step
10. What the final outcome was

If a trace cannot answer these questions, instrumentation is too weak.

## Trace hierarchy

### 1. Workflow trace

At the top level, every user request or system execution should produce one coherent trace.

That trace should capture:

- request identity
- workflow identity
- trust mode
- model profile or deployment profile
- key inputs
- final output
- outcome status
- major substeps

### 2. Node or agent spans

Each meaningful node, stage, or agent action should have its own span.

Examples:

- router decision
- planner step
- retrieval step
- specialist agent execution
- approval request generation
- rejection handling
- final synthesis
- execution tool call

This is usually the most useful level for debugging.

### 3. Tool spans

Every tool call should be visible as a distinct span with:

- tool name
- caller
- relevant arguments
- result summary
- whether side effects occurred
- error information when applicable

### 4. State transition annotations

Not every state mutation needs a separate span, but important state transitions must be visible.

Examples:

- route selected
- confidence level changed
- approval status changed
- user rejection captured
- memory write accepted or rejected
- fallback model chosen
- graph branch changed

## Minimum metadata to attach

Every meaningful trace or span should include useful metadata, not random metadata.

Recommended metadata categories:

### Request metadata

- request ID
- user or session identifier where appropriate
- project/workflow name
- environment
- feature flag or experiment variant
- trust mode

### Model metadata

- provider
- model name
- local vs hosted
- reasoning or non-reasoning profile if relevant
- temperature or key settings only when they matter to analysis

### Architecture metadata

- graph name
- node name
- agent role
- topology variant
- prompt version
- toolset version

### Risk and control metadata

- action category
- approval required or not
- approval state
- side-effect classification
- memory write attempted or not
- sensitive-data handling mode if relevant

### Performance metadata

- latency
- retries
- token usage if available
- cost estimate if available
- concurrency marker when relevant

Metadata should help filtering and comparison later.
Do not attach fields that nobody will inspect.

## What to trace in LLM calls

For important model calls, traces should expose enough to inspect behavior safely.

Capture where appropriate:

- the role of the call in the workflow
- the prompt version or template identity
- the task objective
- relevant context summary
- output structure
- completion status
- latency
- token usage if available
- model chosen
- fallback or escalation behavior if used

Do not store sensitive content unnecessarily.
If the exact content is too sensitive, preserve structured summaries and masked representations.

## What to trace in tool calls

Every tool call should show:

- tool name
- caller node or agent
- argument summary
- full arguments only when safe
- approval requirement
- approval status if applicable
- output summary
- external target if relevant
- whether it was read-only, drafting, or execution
- whether side effects succeeded

This is especially important for:

- sending communications
- deleting data
- writing persistent memory
- modifying records
- triggering external APIs
- filesystem actions
- database writes

## What to trace in approval flows

Human-in-the-loop behavior is central in this repository, so approval traces must be first-class.

For every approval checkpoint, capture:

- the proposed action
- why the system proposed it
- what effect was expected
- whether approval was required by the current trust mode
- whether the user approved, rejected, or ignored it
- what happened after rejection
- whether an alternative path was proposed

A system that “supports approval” but does not trace approval properly is not governable.

## What to trace in rejection handling

Rejection is not just UI behavior.
It is a meaningful signal that should appear clearly in traces.

Capture:

- what action was rejected
- the rejection reason if provided
- the updated constraint inferred from the rejection
- the next alternative proposed
- whether the system avoided retrying the same rejected action

This is especially important when debugging user trust and workflow quality.

## What to trace in memory operations

Memory is high-risk because it can affect future runs.

For memory-related behavior, trace:

- whether a memory read occurred
- why that memory was relevant
- whether a memory write was attempted
- what type of artifact was written
- who or what approved the write policy
- whether the write was accepted, blocked, or revised

Do not silently write to persistent memory.

Do not treat memory as invisible plumbing.

## What to trace in multi-agent systems

When the architecture is multi-agent, observability must show role separation clearly.

For each agent interaction, make visible:

- which agent received the task
- what context was visible to that agent
- what the agent produced
- what it returned to the orchestrator
- whether the handoff or delegation was necessary
- whether a simpler topology might have worked

Bad multi-agent traces make every agent look like the same black box.
Good multi-agent traces make responsibility boundaries obvious.

## Observability rules for prompts and context

Tracing should make prompt and context debugging possible without dumping giant unreadable blobs.

Recommended practice:

- tag the prompt template or version
- record the prompt purpose
- record the major context components
- record state summaries instead of raw long histories when possible
- record whether external or retrieved content was included
- record whether sensitive content was masked

The goal is to diagnose context quality, not to preserve every token forever.

## Structured observability conventions

To keep traces usable, this repository should favor consistent naming.

### Span names

Use names that map to responsibilities, not implementation accidents.

Good examples:

- `route_request`
- `retrieve_customer_context`
- `draft_approval_request`
- `await_user_approval`
- `handle_rejection`
- `send_email`
- `write_memory_entry`

Bad examples:

- `step_1`
- `do_work`
- `helper`
- `run_pipeline_part`

### Tags

Use tags for meaningful slicing dimensions such as:

- feature name
- risk level
- approval mode
- experiment variant
- local-model
- hosted-model
- multi-agent
- single-agent
- destructive-action
- human-in-loop

Do not create dozens of overlapping tags with no stable meaning.

### Trace grouping

Group traces by thread, session, or conversation when that helps diagnose longitudinal behavior.

This is especially important for:

- assistant-style workflows
- approval-rich workflows
- repeated user correction patterns
- memory-driven systems

## Redaction and sensitive data policy

Observability is useful only if it is safe to keep enabled.

This repository should support a masking/redaction strategy for:

- secrets
- credentials
- tokens
- personal identifiers
- sensitive documents
- private message content when not needed for debugging

Recommended posture:

- default to tracing structure and summaries
- include raw content only when it materially improves debugging and is safe
- mask or transform sensitive values before they are persisted in traces
- avoid leaking system prompts, secrets, or unrelated user data into trace metadata

## Tracing depth by environment

### Development

In development, traces should be rich enough to diagnose behavior deeply.

Preferred:
- detailed spans
- prompt and context versioning
- tool argument visibility where safe
- state transition annotations
- manual notes on interesting failures

### Staging

In staging, traces should be close to production while still supporting active debugging.

Preferred:
- realistic approval flows
- representative data volume
- experiment variants
- evaluation hooks
- masking policies close to production

### Production

In production, traces should remain comprehensive enough for diagnosis, but respect privacy, performance, and retention requirements.

Preferred:
- strong metadata discipline
- masking by default
- actionable dashboards
- alerts on meaningful failure patterns
- sampling rules only if volume requires them

Do not turn production into a black box just to reduce clutter.

## Dashboards, alerts, and review flows

Observability is not only about looking at traces one by one.

This repository should also support higher-level views such as:

- success rate by workflow
- approval rejection rate
- unsafe proposal rate
- tool failure rate
- latency by node or model
- cost by workflow variant
- memory write frequency
- fallback model usage
- repeated user correction rate

Alerts should focus on meaningful signals, for example:

- spike in rejected actions
- spike in execution tool failures
- increase in prompt injection-related detections
- sudden latency regression
- routing regression after a prompt or model change
- traces missing required approval checkpoints

## Annotation and review expectations

Useful observability includes human review loops.

Engineers should be able to:

- annotate suspicious traces
- tag traces with failure labels
- queue traces for deeper review
- connect traces to regression datasets
- mark whether a fix hypothesis was confirmed

A trace that revealed a real bug should usually feed one of these:

- a new eval case
- a prompt update
- a tool contract update
- an architecture adjustment
- a policy clarification

## Observability-driven debugging workflow

When a system misbehaves, debug in this order:

1. inspect the end-to-end trace
2. identify the failing span or transition
3. inspect inputs, context, and active prompt identity
4. inspect tool availability and actual tool usage
5. inspect approval policy and user feedback handling
6. inspect memory reads/writes
7. inspect model choice, latency, and cost patterns
8. form a concrete hypothesis
9. test the smallest change that could validate that hypothesis
10. rerun representative traces and evals

Do not jump from “bad result” directly to sweeping architectural change.

## What counts as good trace quality

A good trace in this repository is:

- easy to navigate
- aligned with the real workflow
- explicit about risk boundaries
- explicit about approvals
- explicit about state transitions
- useful for both debugging and evaluation
- safe enough to keep on
- structured enough to compare across runs

A bad trace is:

- too sparse to explain behavior
- too noisy to interpret
- missing approval or tool details
- inconsistent in naming
- missing model or prompt identity
- full of raw data but empty of meaning

## Minimum observability requirements per feature

Before a feature is considered properly instrumented, it should have:

1. one coherent top-level trace per run
2. spans for all important nodes or agent steps
3. spans for all tool calls
4. visible approval checkpoints where relevant
5. visible rejection handling where relevant
6. metadata for workflow, model, and trust mode
7. enough structure to connect a trace to an eval failure
8. a masking strategy if sensitive data may appear

For sensitive features, also require:

- explicit side-effect tagging
- memory write visibility
- external target visibility
- stronger annotation discipline

## Anti-patterns

Avoid these observability mistakes:

- tracing only errors and not successful paths
- tracing only final outputs and not decisions
- giant raw logs with no structure
- hidden tool calls
- hidden memory writes
- no distinction between draft and execute steps
- no approval trace even though approvals exist
- no prompt or prompt-version identity
- mixing unrelated metadata everywhere
- collecting everything but never reviewing anything
- adding dashboards without enough trace quality underneath

## Final design stance

Observability in this repository is not an afterthought.
It is how an LLM-first system becomes inspectable enough to improve.

The model is allowed to stay flexible where semantics matter.
Tracing is what lets that flexibility remain governable.

If the system made a decision, called a tool, crossed a trust boundary, asked for approval, handled a rejection, or changed state, an engineer should be able to see that clearly later.

That is the standard.

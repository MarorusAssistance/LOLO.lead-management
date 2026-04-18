# Evaluation Strategy for LLM-First Agentic Systems

## Purpose

This document defines how evaluation should be designed and applied in this repository.

The goal of evaluation is not to produce vanity metrics. The goal is to detect whether a system is actually getting better, staying safe, and behaving more reliably as architecture, prompts, tools, models, and workflows evolve.

This repository builds LLM-first systems. That means evaluation must cover more than final text quality. It must also cover:

- whether the system chose the right path
- whether the system used tools appropriately
- whether approvals were respected
- whether the system handled rejection correctly
- whether the system remained safe under adversarial or ambiguous input
- whether changes improved the core use case instead of just making outputs sound nicer

## Evaluation principles

### 1. Evaluation starts early

Do not wait until the system is “finished” before adding evals.

Tracing and evaluation should appear during feature development, not after it.

For each meaningful feature or workflow phase, add at least:

- a happy-path evaluation
- a safety or policy evaluation
- a workflow or tool-usage evaluation when applicable

### 2. Evaluate behavior, not just answers

A final answer can look good even when the system took the wrong path.

For agentic systems, evaluation should cover at least three levels:

- final response quality
- trajectory quality
- critical step quality

### 3. Tie every meaningful change to evidence

Do not accept major prompt, tool, or architecture changes just because they “feel better”.

Changes should be justified by one or more of the following:

- a trace showing failure
- an eval regression
- a repeated user complaint
- a concrete unhandled edge case
- a cost, latency, or reliability problem observed in practice

### 4. Keep evals aligned with the actual product goal

This repository optimizes for user productivity, correctness, safety, and controllable autonomy.

Evaluation should therefore reflect:

- whether the system materially helps the user
- whether it respects the configured trust mode
- whether it avoids unnecessary friction
- whether it avoids dangerous or misleading behavior
- whether it improves the workflow rather than just producing plausible language

### 5. Evaluate the system that really exists

Do not evaluate a toy version if production behavior depends on:

- multiple agents
- specific tool access
- approval gates
- long context
- local models
- low-budget constraints
- concurrency
- retrieval
- stateful workflows

If the real system uses those elements, evals should exercise them.

## LangSmith evaluation model

This repository uses LangSmith as the main evaluation and observability layer.

A practical evaluation loop in LangSmith is built from these parts:

- **dataset**: a set of representative inputs, and optionally references
- **target function**: the part of the application being tested
- **evaluators**: scoring logic applied to outputs and behavior
- **experiments**: recorded runs of a version of the system against a dataset

LangSmith’s documentation explicitly frames evaluation around datasets, target functions, evaluators, and experiments, and supports both offline and online evaluation workflows. It also supports comparing experiments and connecting them to execution traces. citeturn708689search0turn708689search3turn708689search7

## Evaluation levels for this repository

### Level 1. Final response evaluation

Use this when you want to know whether the user-facing result is good.

Examples:

- did the system answer correctly?
- did it extract the right entities?
- did it draft the right email?
- did it produce a useful plan?
- did it respect formatting requirements?

This is necessary but not sufficient.

A good final answer does not prove the system behaved well internally.

### Level 2. Trajectory evaluation

Use this when path matters, which is common in agentic systems.

Examples:

- did the agent call the right tools?
- did it avoid unnecessary tool calls?
- did it route the task to the correct specialist?
- did it ask for approval before executing a risky action?
- did it recover appropriately after rejection?

LangSmith provides agent-focused evaluation patterns specifically for trajectory, meaning evaluation of the sequence of steps and tool calls an agent took, either with exact matching or judge-based scoring. citeturn708689search2turn708689search6turn708689search9

### Level 3. Single-step evaluation

Use this when one step is especially important or failure-prone.

Examples:

- was the first tool selection appropriate?
- did the model classify the request into the right route?
- did it summarize the user rejection correctly?
- did it identify the relevant evidence from context?
- did it generate a safe approval request?

This is especially useful when a full workflow is hard to debug as one monolithic unit.

## What should always be evaluated

For most features in this repository, evaluation should cover the following dimensions.

### 1. Core task success

Did the system complete the task the user actually cares about?

Examples:

- correct information found
- correct draft produced
- correct recommendation generated
- correct workflow branch selected

### 2. Tool choice quality

Did the system use tools only when needed, and the right ones?

Questions:

- Was tool use necessary?
- Was the right tool selected?
- Were arguments sufficiently complete and correct?
- Were unnecessary calls avoided?

### 3. Approval policy compliance

Did the system respect the configured trust mode?

Questions:

- Did it ask for approval when required?
- Did it avoid asking for approval when not required?
- Did it separate proposal from execution clearly?
- Did it avoid claiming execution before execution occurred?

### 4. Rejection handling quality

If the user rejected an action:

- Did the system interpret the rejection correctly?
- Did it preserve the rejection signal in state?
- Did it propose a better alternative?
- Did it avoid blindly repeating the same action?

### 5. Safety and prompt injection resilience

Did the system remain aligned when faced with:

- malicious instructions in user input
- malicious instructions in retrieved documents
- manipulative external content
- misleading context
- requests that should require stronger controls

### 6. Cost and latency reasonableness

Did the system achieve quality efficiently enough for the actual deployment constraints?

This matters especially when:

- using local models
- using expensive frontier models
- running multi-agent topologies
- executing concurrent tool workflows

### 7. Trace quality

Did the run produce enough trace information to diagnose failures later?

A feature is not really production-grade if it works but is opaque.

## Dataset strategy

### Build datasets from real failure modes first

The first dataset should not be synthetic perfection.

Start with:

- real user tasks
- real confusing inputs
- real edge cases
- real failures observed in traces
- real approval/rejection scenarios

Synthetic data can help later, but it should not replace grounded examples.

### Include representative variability

Datasets should reflect:

- easy cases
- normal cases
- ambiguous cases
- adversarial or malicious cases
- edge cases
- rejection and correction cases
- cases with missing information
- cases that should not use tools
- cases that should use tools
- cases that should pause for approval

### Keep datasets versioned by intent

It is often useful to maintain separate datasets for:

- architecture and routing
- prompt behavior
- tool selection
- approval logic
- safety behavior
- regression coverage for prior bugs

This makes failures easier to interpret than one giant mixed dataset.

### Prefer compact, high-signal datasets over bloated weak ones

A small dataset that captures real failure modes is more valuable than a huge dataset full of repetitive happy-path cases.

## Evaluator strategy

This repository should use multiple evaluator types where appropriate.

### 1. Rule-based evaluators

Best for:

- schema validation
- exact fields
- approval presence
- whether a required tool was called
- whether a forbidden action occurred
- cost and latency thresholds
- required structure in outputs

Use rule-based evaluators when correctness is objectively checkable.

### 2. Reference-based evaluators

Best for:

- extraction tasks
- classification tasks
- bounded drafting tasks
- route selection
- structured outputs with known expected values

These compare against known expected answers or labels.

### 3. LLM-as-judge evaluators

Best for:

- usefulness
- relevance
- tone appropriateness
- quality of alternatives
- whether reasoning artifacts are sufficient
- whether a proposed action fits the user’s rejection

Use carefully and with clear rubrics.
Judge prompts should be explicit, stable, and tested.

### 4. Human review

Use when:

- stakes are high
- the task is subjective
- the output affects strategy or communication
- the rubric is not yet mature
- you are validating whether the eval itself makes sense

Human review is especially useful early in a feature’s life.

## Evals by workflow stage

### During architecture design

Evaluate:

- whether the chosen topology is actually needed
- whether multi-agent complexity improved something measurable
- whether a simpler design would perform as well

This is often done through comparative experiments rather than one static score.

### During prompt iteration

Evaluate:

- final output quality
- route selection
- approval behavior
- rejection handling
- robustness to ambiguity

Do not iterate prompts blindly.
Use traces and evals to identify what changed.

### During tool integration

Evaluate:

- tool choice accuracy
- argument quality
- rate of unnecessary calls
- failure handling
- read vs write separation
- side-effect gating

### During security hardening

Evaluate:

- prompt injection resistance
- indirect instruction handling
- refusal or escalation behavior
- approval compliance under adversarial content
- memory write behavior under malicious inputs

### During production monitoring

Evaluate:

- drift in success rate
- drift in latency or cost
- increase in approval errors
- increase in unsafe proposals
- increase in repeated user corrections

LangSmith supports both offline evaluation and online monitoring/evaluation workflows tied to observability. citeturn708689search3turn708689search4turn708689search5

## Core evaluation suites to maintain

At minimum, this repository should maintain these suites.

### Suite 1. Happy-path functionality

Purpose:
- confirm core value delivery

Examples:
- standard user requests
- routine drafting tasks
- normal retrieval tasks
- standard approval and execution flows

### Suite 2. Edge-case behavior

Purpose:
- confirm resilience without brittle overfitting

Examples:
- ambiguous requests
- incomplete data
- conflicting signals
- multi-step requests with mixed intent

### Suite 3. Safety and policy

Purpose:
- confirm compliance with repository constraints

Examples:
- prompt injection attempts
- requests to skip approval
- malicious retrieved instructions
- attempts to trigger destructive actions without clear authority

### Suite 4. Tool and routing quality

Purpose:
- confirm the system chooses the right path

Examples:
- correct agent selected
- correct tool selected
- no unnecessary specialist escalation
- no unnecessary tool call
- proper tool arguments

### Suite 5. Regression coverage

Purpose:
- prevent known failures from returning

Examples:
- previously broken edge case
- prior approval bug
- prior routing bug
- prior hallucinated completion signal
- prior unsafe side-effect attempt

## Comparative evaluation strategy

When changing architecture, prompts, models, or tools, prefer comparative experiments.

Compare versions across:

- success rate
- trajectory quality
- approval compliance
- safety behavior
- latency
- token or cost profile
- user-facing usefulness

LangSmith organizes results into experiments associated with a dataset and supports side-by-side comparison, which is exactly the right model for this kind of controlled iteration. citeturn708689search7

## Failure analysis workflow

When an eval fails, debug in this order:

1. inspect the trace
2. identify the failing step
3. decide whether the issue is prompt, context, tool contract, model capability, routing, memory, or architecture
4. form a concrete hypothesis
5. propose the smallest change that could test that hypothesis
6. rerun targeted evals
7. only then consider broader refactors

This repository should not jump directly from “eval failed” to “rewrite architecture”.

## Evaluation guidance for human-in-the-loop systems

Because many systems in this repository use human approval modes, evals must explicitly test approval behavior.

### Cases to include

- action should execute autonomously and does
- action should pause for approval and does
- action should not require approval and does not ask unnecessarily
- user rejects the action and the system adapts well
- user rejects the action and the system should stop
- action should be redrafted before execution
- model incorrectly implies execution before approval

### What to score

- correctness of pause/continue behavior
- clarity of approval request
- faithfulness of action preview
- adaptation after rejection
- avoidance of repeated disallowed behavior

## Local-model and budget-aware evaluation

Many projects in this repository may use local models or strict budgets.

Evaluation should therefore consider deployment constraints, not only ideal-model performance.

Useful questions:

- Does multi-agent design still outperform a simpler design under limited context?
- Does the local model need more decomposition or narrower prompts?
- Does the expensive model justify its extra cost?
- Can a smaller model handle drafting while a stronger model handles hard reasoning?
- Is concurrency helping enough to justify complexity?

Evaluation should help answer these tradeoffs, not ignore them.

## Trace-linked evaluation standards

Every important eval should be easy to connect back to traces.

A good experiment should make it easy to inspect:

- what prompt version was used
- which model was used
- which tools were available
- which path the workflow took
- what approval state was active
- what exact step failed

LangSmith’s observability model captures traces as step-by-step records of execution, which is why tracing must be enabled early and kept comprehensive. citeturn708689search1turn708689search22turn708689search8

## Minimum evaluation requirements per feature

Before a feature is considered properly integrated, it should have:

1. at least one happy-path eval
2. at least one edge-case or ambiguity eval
3. at least one safety or policy eval
4. at least one trace from a representative run
5. at least one tool/routing eval if tools or routing are involved
6. explicit documentation of what changed and why

For sensitive features, add:

- approval compliance evals
- destructive-action blocking or gating evals
- rejection handling evals
- prompt injection evals

## Anti-patterns

Avoid these evaluation mistakes:

- scoring only final text quality
- evaluating prompts without traces
- using only easy cases
- adding one giant synthetic benchmark and calling it enough
- changing architecture without comparative evidence
- letting LLM judges score vague criteria with no rubric
- ignoring latency and cost in real deployment contexts
- treating approvals as UX only instead of policy behavior
- failing to add regressions for previously fixed bugs
- using evals only at the end of the project

## Repository default workflow for eval-driven iteration

For each feature or iteration:

1. define the target behavior
2. define likely failure modes
3. add tracing
4. add a minimal evaluation set
5. implement the smallest viable version
6. inspect traces on representative runs
7. improve prompt, context, tools, or topology based on evidence
8. compare experiments when behavior changes meaningfully
9. add regression cases for any bug you fix
10. keep the dataset sharp and relevant

## Final design stance

In this repository, evaluation is not a side activity.

It is the mechanism that keeps an LLM-first system honest.

Good evals do not force the system to become rigid.
They reveal whether the current mix of prompts, models, tools, approvals, and architecture is actually producing the behavior you want.

If a system cannot be traced and evaluated, it is not ready to be trusted.

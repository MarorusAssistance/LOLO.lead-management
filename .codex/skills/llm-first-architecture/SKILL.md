---
name: llm-first-architecture
description: Use when designing, scoping, or refactoring an AI system in Python. Focus on deciding whether the solution should be a simple automation, a workflow with one agent, a partially agentic system, or a multi-agent architecture. Prioritize LLM-first reasoning, human-in-the-loop control, LangGraph/LangChain/LangSmith alignment, and evidence-driven architectural choices.
---

# LLM-First Architecture Skill

## Purpose

Use this skill when the task involves any of the following:

- deciding whether a use case really needs AI
- deciding whether the system should be:
  - a simple automation
  - a workflow with one LLM-powered step
  - a single agent with tools
  - a multi-agent system
  - a stateful LangGraph workflow
- deciding where semantic reasoning belongs and where deterministic code belongs
- planning an MVP and staged roadmap
- refactoring a solution that has drifted into code-first logic
- reviewing whether current architecture matches budget, model constraints, context limits, and user-trust requirements

This skill should guide architectural reasoning before implementation starts.

## When to use

Use this skill when:

- the user asks for architecture or system design
- the task is ambiguous and the architecture is not yet decided
- a feature could be solved in multiple ways
- there is a risk of overengineering with multi-agent design
- there is a risk of underengineering with a single overloaded agent
- the project involves LangChain, LangGraph, LangSmith, tool calling, or human approval flows
- the current solution relies too much on deterministic branching for semantic work
- the system must balance autonomy with approval controls
- model cost, context window, latency, or local-vs-hosted constraints matter

## When not to use

Do not use this skill for:

- isolated bug fixes
- tiny refactors that do not affect architecture
- simple syntax or dependency fixes
- writing tests for already agreed architecture
- making implementation-level edits where the system shape is already settled

If the architecture is already decided and the task is now implementation-specific, use a more focused implementation skill instead.

## Design stance

This repository is LLM-first, not code-first.

That means:

- semantic, fuzzy, language-heavy, judgment-heavy decisions should stay with the model whenever that is sensible
- deterministic code should exist to support correctness, integration, safety, approvals, state transitions, persistence, and side-effect boundaries
- the system should not be made rigid just to compensate for weak prompts, weak context engineering, or weak architecture
- architecture should maximize user productivity while keeping the human in control when risk matters

This skill must actively resist the following failure mode:

> turning an agentic or semantic problem into a maze of hardcoded branches, heuristics, filters, or pseudo-sanitizers that make the system more brittle without truly improving it

## Default reasoning order

When using this skill, reason in this order.

### Step 1. Decide whether AI is justified at all

Before designing an agentic system, test whether the problem actually needs an LLM.

Ask implicitly:

- Is there real semantic ambiguity?
- Is the task language-heavy?
- Does the system need interpretation, drafting, ranking, suggestion, synthesis, or adaptive decision-making?
- Would a deterministic automation already solve the core need cleanly?
- Is the user asking for AI even though a simpler automation may be better?

If the task does not benefit meaningfully from LLM behavior, say so clearly.
You may still follow the developer’s direction, but first state that a simpler approach may be better.

### Step 2. Understand constraints

Identify the real constraints before choosing topology.

Always inspect or infer:

- budget level
- model quality available
- local vs hosted models
- context window constraints
- latency expectations
- concurrency needs
- privacy or deployment constraints
- user trust expectations
- whether the system must support approval-heavy flows
- whether the task involves destructive or externally side-effecting actions

Architecture without constraints is not real architecture.

### Step 3. Identify where the LLM should exist

Do not assume the whole system must be agentic.

Decide whether the LLM belongs in:

- only one semantic classification or extraction step
- one or more drafting/suggestion steps
- a single agent with tool calling
- an orchestrated workflow with explicit state
- a multi-agent topology with role separation
- no part of the system at all

The question is not “how do I make it multi-agent”.
The question is “where does semantic intelligence actually add value”.

### Step 4. Choose the minimum viable topology

Choose the smallest architecture that cleanly fits the job.

Possible outcomes:

#### A. Simple automation
Use when the task is deterministic and does not benefit meaningfully from LLM interpretation.

#### B. Automation with one LLM-powered semantic step
Use when most of the workflow is deterministic but one step genuinely needs interpretation, drafting, ranking, or synthesis.

#### C. Single agent with tools
Use when one coherent role can handle the task and the tools all belong to a related domain.

Prefer this when:
- one agent can keep enough context
- capabilities are related
- budget and latency favor simplicity
- a stronger model can solve the full task well

#### D. Workflow with one agent inside it
Use when the flow itself is structured, but one or more stages benefit from an LLM agent or semantic step.

#### E. Multi-agent orchestration
Use only when there is a real benefit from role separation.

Typical reasons:
- context window pressure
- local model limits
- clearly different specialties
- clearly different toolsets
- different trust or permission boundaries
- a need for narrower prompts and isolated memory

Do not choose multi-agent just because the task is complex.
Choose it only when separation materially improves quality, control, or feasibility.

### Step 5. Define human control model

Always define the trust mode.

Supported modes in this repository are:

- full trust / autonomous execution
- partial trust / approval only for final or side-effecting actions
- low trust / approval for every meaningful action

If a user rejects an action, the system should not simply stop or repeat itself.
It should update its working plan and search for another path that respects the rejection.

Architecture must explicitly support this behavior.

### Step 6. Plan delivery in phases

Do not design the whole perfect system as version one.

Break the project into staged delivery:

- MVP that validates the core value
- iteration to handle major edge cases
- next feature or next specialist capability
- further hardening with tracing, evals, and approval behavior

Prefer phased architecture that supports learning over speculative completeness.

## Decision rules for LLM vs deterministic code

Use the LLM by default for:

- semantic classification
- interpreting messy user intent
- extracting meaning from unstructured language
- searching or identifying relevant information semantically
- drafting or rewriting
- generating suggestions or alternatives
- choosing among plausible next paths when judgment is required
- synthesizing information across multiple sources

Use deterministic code by default for:

- authentication
- authorization
- API plumbing
- persistence
- transport concerns
- UI state management
- explicit approval gating
- irreversible side effects
- stable state transitions
- typed validation of operational inputs/outputs
- concurrency control where correctness depends on ordering

Do not move semantic ambiguity into deterministic code just because it feels safer.
Do not move irreversible execution into pure model choice just because it feels elegant.

## Decision rules for topology

### Prefer a single agent when

- one coherent responsibility exists
- the tools serve related goals
- context can fit comfortably
- the model is strong enough
- latency simplicity matters more than decomposition
- specialist separation would add more complexity than value

### Prefer multi-agent when

- one agent would be overloaded with unrelated responsibilities
- the context window is a real problem
- model capacity is limited
- specialties are materially different
- tool access should differ by role
- memory and prompts should be isolated
- orchestration improves reliability more than it harms latency/cost

### Prefer workflow + semantic steps when

- the process is mostly structured
- only a few points need LLM judgment
- the rest benefits from explicit deterministic flow
- approval checkpoints are central
- the system needs clarity more than autonomy

## Required architectural outputs

When using this skill, the response should usually produce the following.

### 1. Recommendation summary

State the recommended architecture clearly.

Example categories:
- no AI needed
- automation + semantic step
- single agent with tools
- workflow with one agent
- multi-agent orchestration

### 2. Why this topology fits

Explain using the actual constraints:

- budget
- model strength
- local vs hosted
- context limits
- latency
- trust mode
- tool diversity
- task diversity

### 3. Why other options are worse

Contrast the recommended option against at least the most plausible alternatives.

Examples:
- why not pure deterministic automation
- why not a single general-purpose agent
- why not multi-agent yet
- why not a larger hosted model with simpler topology

### 4. LLM placement map

Explain exactly where the LLM belongs and why.

For each major subsystem or step, identify:
- semantic / model-driven
- deterministic / code-driven
- mixed / approval-gated

### 5. Human-in-the-loop design

State:
- which trust mode is assumed
- which actions require approval
- what happens on rejection
- how approval affects tool exposure or execution flow

### 6. Phase plan

Break the solution into:
- MVP
- first iteration
- next capability expansion
- hardening or scaling steps

### 7. Observability and eval implications

State what must exist from the beginning:
- tracing
- initial evals
- edge-case coverage
- approval flow tests
- routing/tool-choice evaluation if relevant

## Required behavior during reasoning

When this skill is active:

- challenge whether AI is needed at all
- challenge whether multi-agent is actually necessary
- challenge whether a single agent is being overloaded
- challenge whether deterministic branching is replacing semantic reasoning unnecessarily
- challenge whether the architecture fits the real model and budget constraints
- challenge whether approval behavior is properly designed
- challenge whether the design is being overbuilt before validating the core use case

Do not flatter the user’s first idea if it is weak.
State clearly why it is weak and what would be better.

## How to handle ambiguity

If the architecture depends on information that is missing, do not guess silently when the missing detail is load-bearing.

Examples of load-bearing ambiguity:

- available model quality
- local vs hosted deployment
- hard privacy restrictions
- whether external side effects are allowed autonomously
- latency budget
- whether two capabilities must share context or should be split

When ambiguity matters, present the best options and ask the user to choose.

Prefer this structure:

- Option A
- Option B
- trade-offs
- what would change architecturally

## Anti-patterns to avoid

Avoid all of the following unless there is strong evidence otherwise:

- defaulting to multi-agent because it sounds more advanced
- defaulting to one god-agent with too many unrelated tools
- turning semantic judgment into if/else trees
- adding many sanitizers, regexes, or filters to patch weak prompting
- using prompts to hide broken architecture
- using architecture to hide weak tool contracts
- mixing proposal and execution in sensitive workflows
- skipping approval design and adding it later as an afterthought
- persisting raw reasoning as shared memory
- designing the full final system before validating the core workflow
- proposing major refactors without evidence from traces or evals

## Documents to consult

When using this skill, consult these repository documents if available:

- `docs/agent-engineering/architecture-principles.md`
- `docs/agent-engineering/prompting.md`
- `docs/agent-engineering/threat-model.md`
- `docs/agent-engineering/tool-contracts.md`
- `docs/agent-engineering/eval-strategy.md`
- `docs/agent-engineering/tracing-observability.md`

Use them to keep design aligned with repository policy.

If key guidance is not available in the repository and the answer depends on up-to-date framework behavior, search authoritative documentation before deciding.

## Output format

When responding under this skill, prefer this structure:

1. **Problem framing**
2. **Constraints that matter**
3. **Recommended architecture**
4. **Why this is the best fit**
5. **Why the main alternatives are worse**
6. **Where the LLM belongs**
7. **Human approval model**
8. **MVP and iteration plan**
9. **Main risks and edge cases**
10. **What should be traced and evaluated first**

Keep the response concrete.
Do not jump to code unless the architecture is already clear.

## Final stance

This skill exists to keep the assistant from drifting into two bad extremes:

- over-deterministic code-first systems that suffocate the value of the LLM
- over-agentic systems that use multi-agent complexity where a simpler design would work better

The right answer is the smallest architecture that uses the LLM where semantics matter, uses code where guarantees matter, keeps the human in control when risk matters, and remains observable enough to improve with evidence.

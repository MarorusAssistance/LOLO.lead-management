---
name: refactor-code-first-to-llm-first
description: Use when reviewing or refactoring an AI system that has become too deterministic, heuristic-heavy, or code-first. Focus on recovering the right role for the LLM, removing brittle semantic logic from code, preserving necessary hard guarantees, and redesigning prompts, context, tools, and workflow boundaries to produce a cleaner LLM-first system.
---

# Refactor Code-First to LLM-First Skill

## Purpose

Use this skill when the task is to review, critique, or refactor a system that technically uses LLMs but has drifted into an overly deterministic design.

Typical symptoms include:

- semantic decisions implemented as large if/else trees
- excessive regexes, keyword rules, or sanitizers for language understanding
- giant routing logic outside the model
- prompt behavior patched by ever-growing code heuristics
- one workflow that should be model-driven but is fragmented into brittle micro-rules
- an agent that technically exists but is prevented from making the decisions it should be making
- duplicated logic split across prompts, code branches, and tool wrappers
- systems that “work” on narrow cases but become hard to evolve, debug, or generalize

This skill should help recover a cleaner LLM-first design without removing the hard guarantees that genuinely belong in code.

## When to use

Use this skill when:

- the user says the current system feels too deterministic
- semantic behavior has been pushed into code
- prompts are weak because code is trying to do the model’s job
- the system has become difficult to extend because logic is scattered across rules
- the assistant is about to add more heuristics to patch a semantic issue
- routing, extraction, ranking, suggestion, or next-step selection are being hardcoded
- a LangChain or LangGraph system needs to be redesigned so the LLM handles the right layer
- a local model is being overconstrained rather than properly decomposed
- the current implementation is “safe” only because it overblocks useful behavior

## When not to use

Do not use this skill for:

- removing necessary hard controls around destructive or high-impact actions
- replacing authentication, authorization, persistence, or approval gates with prompts
- weakening tool contracts just to make the system feel more agentic
- turning deterministic business rules into vague model behavior when guarantees truly matter
- tiny implementation refactors with no architecture or prompting implications

If the current code is deterministic because the task genuinely requires hard guarantees, say so clearly and do not try to force an LLM-first refactor.

## Design stance

This repository prefers LLM-first systems, not code-first systems.

That means:

- semantic ambiguity should usually be handled by the model
- language-heavy interpretation should usually be handled by the model
- drafting, suggestion, synthesis, and path selection should usually be handled by the model
- deterministic code should remain where guarantees, side effects, permissions, and state integrity matter

This skill must actively resist two opposite mistakes:

### Mistake 1. Over-deterministic code-first design
Examples:
- using regexes and keyword lists to emulate language understanding
- routing based on brittle string checks instead of semantic classification
- burying all intelligence in helper functions and leaving the LLM with little real responsibility
- patching every prompt failure with another branch

### Mistake 2. Naive overcorrection
Examples:
- removing necessary validation for execution tools
- letting the model execute destructive actions without gates
- replacing stable business invariants with fuzzy prompting
- turning every reliable workflow step into an unconstrained model decision

The goal is not “more LLM everywhere.”
The goal is “LLM where semantics matter, code where guarantees matter.”

## Core mental model

When using this skill, evaluate the current system as a separation problem:

- what is currently done in code?
- what part of that is truly deterministic?
- what part is actually semantic or judgment-heavy?
- what part should move into prompts, context design, output schemas, or agent nodes?
- what part should remain in code because it protects safety, correctness, approvals, or persistence?

A good refactor usually does not remove code randomly.
It relocates responsibilities to the correct layer.

## Default refactoring order

When using this skill, reason in this order.

### Step 1. Map the current behavior

Before changing anything, identify where logic currently lives:

- prompt instructions
- runtime context assembly
- model output parsing
- routing code
- tool wrappers
- external service adapters
- validation layers
- graph transitions
- memory logic
- approval flow

Do not propose a refactor before locating the real concentration of brittle logic.

### Step 2. Identify semantic logic that drifted into code

Look for code that is trying to decide things that are naturally semantic, such as:

- intent classification
- category selection from messy language
- choosing the best path from multiple plausible options
- finding relevant information in noisy text
- suggesting next steps
- drafting or rewriting with tone/context sensitivity
- evaluating which tool is most appropriate based on meaning

These are prime candidates to move back toward the model layer.

### Step 3. Identify code that must stay deterministic

Preserve deterministic logic for:

- authentication and authorization
- persistence
- explicit approvals
- irreversible side effects
- transport/integration concerns
- strong tool input validation
- stable state transitions
- concurrency safety
- model-independent business invariants
- compliance or permission boundaries

Do not weaken these just because the system is being made more LLM-first.

### Step 4. Identify architectural smell, not only code smell

A code-first system often points to deeper architectural problems such as:

- one god-agent with too many unrelated tools
- one workflow trying to solve multiple unrelated tasks
- no shared state model
- no structured outputs
- poor context engineering
- weak tool contracts
- no approval separation between proposal and execution
- no traceability, so developers patch blindly with more code

The right fix may be:
- better prompts
- clearer state
- fewer visible tools
- role separation
- LangGraph decomposition
- better tool contracts
- structured outputs
- better approval flow

not just “delete code.”

### Step 5. Move logic to the right layer

For each brittle behavior, decide the correct destination:

#### Move to prompt/system/task instructions when:
- the rule is semantic
- the issue is judgment quality
- the behavior is about how to interpret or respond
- the system needs better policy clarity rather than stricter parsing

#### Move to context engineering when:
- the model sees irrelevant history
- the model lacks key user constraints
- retrieved evidence is too noisy
- rejection signals are not preserved
- the wrong state slice is being passed

#### Move to structured outputs when:
- free text is causing unstable downstream behavior
- the system needs bounded route choices
- tools need predictable arguments
- approvals need a stable artifact

#### Move to tool design when:
- tools are too broad
- read and write actions are mixed
- draft and execute are mixed
- tool descriptions are unclear
- arguments are too vague

#### Move to graph/workflow design when:
- transitions are hidden in helper logic
- approvals are ad hoc
- retries are chaotic
- rejection handling is not a first-class path
- one node is doing too many things

#### Keep in deterministic code when:
- the action crosses a real control boundary
- the behavior must be guaranteed
- failure would cause real system harm
- the logic is not semantic but structural

## Refactoring heuristics

Use these heuristics when deciding whether logic should move out of code.

### Good candidate to move toward the LLM

If the current code answers questions like:
- “what did the user probably mean?”
- “which option best matches the request?”
- “what information in this text is relevant?”
- “how should this be worded?”
- “what is the best next path given messy context?”
- “which of these several plausible actions fits best?”

that logic is often better handled by the model.

### Good candidate to keep deterministic

If the current code answers questions like:
- “is this user allowed to do this?”
- “was approval granted?”
- “did the external API return success?”
- “is the record present?”
- “did the state transition occur?”
- “is this destructive action allowed right now?”
- “does this schema validate?”

that logic should usually stay in code.

## Required outputs when this skill is used

When responding under this skill, usually provide the following.

### 1. What is too code-first

State the main places where semantic work has drifted into code.

Examples:
- heuristic routing
- regex-heavy extraction
- branch explosion
- duplicated prompt policy in code
- overblocking validations
- tool wrappers doing semantic classification internally

### 2. What must remain deterministic

State the hard boundaries that should stay in code.

Examples:
- approvals
- side-effect gating
- permissions
- persistence
- state integrity
- execution confirmation

### 3. Recommended redistribution of responsibilities

For each major concern, say whether it should live in:
- prompt
- runtime context
- structured output schema
- tool contract
- graph topology / node design
- deterministic code

### 4. Refactor plan

Provide an ordered plan such as:
1. remove brittle heuristic from routing
2. replace with bounded semantic classifier
3. reduce toolset visible to the agent
4. separate draft and execute tools
5. preserve approval gate
6. add traces and targeted evals

### 5. Why the new design is better

Explain improvements in:
- generalization
- maintainability
- observability
- user productivity
- reduced rule sprawl
- better fit for local vs hosted models
- better alignment with trust mode

### 6. What to trace and evaluate next

State:
- which traces should be inspected
- which regression cases should be added
- whether route quality, tool quality, or approval quality should be evaluated

## Refactoring patterns to favor

### Pattern 1. Heuristic router -> semantic router with bounded schema

Bad:
- long keyword lists or regex rules for routing

Better:
- LLM route selection with a small allowed set of route labels
- clear route descriptions
- explicit schema
- traces and evals for route quality

### Pattern 2. Giant general prompt + code patches -> narrower prompts + cleaner context

Bad:
- one vague prompt, then code keeps patching model mistakes

Better:
- clearer role prompt
- cleaner state slice
- stronger output contract
- fewer reactive code hacks

### Pattern 3. One mixed tool -> read / draft / execute separation

Bad:
- one tool both prepares and performs a risky action

Better:
- `get_*`
- `draft_*`
- `request_approval`
- `execute_*`

### Pattern 4. Hidden semantic helper logic -> explicit agent or node responsibility

Bad:
- helper function doing hidden classification, search, and decision logic

Better:
- explicit semantic node
- explicit output artifact
- visible transition
- easier tracing and evaluation

### Pattern 5. Overgrown single agent -> narrower role split only when needed

Bad:
- one agent with unrelated responsibilities and too many tools

Better:
- keep one agent if one role is coherent
- split into specialists only when context, permissions, or capabilities genuinely differ

### Pattern 6. Raw text outputs -> structured artifacts

Bad:
- parsing prose to recover decisions

Better:
- typed route choice
- typed approval artifact
- typed extracted fields
- typed execution payload

## Review checklist

When this skill is active, review the current system against these questions.

1. What semantic work is currently hardcoded?
2. Why was it hardcoded?
3. Is the real problem weak prompting, weak context, or weak architecture?
4. Which code rules are actually protecting a real control boundary?
5. Which rules are only compensating for poor model integration?
6. Are draft and execute clearly separated?
7. Are approvals explicit?
8. Are tool contracts too broad?
9. Is the agent overconstrained because it sees too much irrelevant context?
10. Would a smaller, clearer graph improve more than another patch?
11. Are traces strong enough to justify the refactor?
12. What evals will prove the refactor is an improvement?

## Anti-patterns to avoid

Avoid all of the following unless there is strong evidence otherwise:

- deleting necessary guardrails in the name of being more agentic
- moving permissions or destructive checks into prompts
- replacing typed outputs with free-form prose
- turning stable invariants into model guesses
- adding yet another heuristic before checking whether the model should own the decision
- using regex to emulate language understanding when semantic classification would be cleaner
- letting a tool wrapper become a hidden deterministic router
- keeping giant mixed tools that combine proposal and execution
- refactoring without traces or evals
- assuming “more agents” is the fix for every code-first smell

## Documents to consult

When this skill is active, consult these repository documents if available:

- `docs/agent-engineering/architecture-principles.md`
- `docs/agent-engineering/prompting.md`
- `docs/agent-engineering/threat-model.md`
- `docs/agent-engineering/tool-contracts.md`
- `docs/agent-engineering/eval-strategy.md`
- `docs/agent-engineering/tracing-observability.md`

Use them to keep the refactor aligned with the repository’s architecture, prompting, security, tool, evaluation, and observability standards.

If current framework behavior matters, consult authoritative documentation before assuming the right refactoring pattern.

## Output format

When responding under this skill, prefer this structure:

1. **Where the current design is too code-first**
2. **What should stay deterministic**
3. **What should move to the LLM / prompts / context**
4. **What should move to tools or graph design**
5. **Recommended refactor sequence**
6. **Main risks of the refactor**
7. **What to trace and evaluate to validate it**

Keep the response concrete and layered.
Do not just say “make it more LLM-first.”
Explain exactly which responsibilities should move, and which should not.

## Final stance

This skill exists to keep the assistant from making the same bad move twice:

first by over-hardcoding semantic behavior,
and then by overcorrecting and removing the hard guarantees that actually matter.

A good refactor makes the system more LLM-first where semantics matter, more explicit where workflow matters, and just as disciplined where control boundaries matter.

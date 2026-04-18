# Architecture Principles

## Purpose

This document defines the architectural principles for building AI systems in this repository.

The goal is not to force every project into a multi-agent pattern, nor to replace software engineering with prompts. The goal is to design the smallest, clearest, and most capable system that fits the real needs of the project while following an LLM-first philosophy when the problem contains semantic, fuzzy, or context-dependent decisions.

These systems should maximize the productivity of the end user, preserve user control over sensitive actions, and remain observable, testable, and secure.

---

## Core Philosophy

### 1. Start from the problem, not from the pattern
Do not assume that every problem needs:
- a multi-agent system,
- an autonomous agent,
- a workflow engine,
- retrieval,
- or even an LLM.

First determine what the user or client is actually trying to achieve.

If the real need is a simple deterministic automation, suggest it. If the real need benefits from semantic understanding, planning, tool selection, content synthesis, or ambiguous decision-making, then bring in an LLM where it adds real value.

### 2. Prefer LLM-first when the task is semantic
When a problem is best solved through interpretation, ranking, extraction under ambiguity, suggestion, planning, or contextual reasoning, the decision should stay in the LLM unless there is a strong reason to hard-code it.

Typical examples:
- identifying what the user wants from semi-structured information,
- deciding the best next action among several context-dependent options,
- generating suggestions or drafts,
- selecting relevant information from noisy sources,
- routing tasks based on semantic intent.

Do not replace these decisions with brittle regex chains, heuristics, or large forests of `if/else` logic unless there is a proven reliability or policy reason.

### 3. Use deterministic code where correctness must not drift
Code should provide stability where LLM behavior is not sufficient on its own.

Typical deterministic responsibilities:
- authentication and authorization,
- API and database integration,
- persistence,
- tool invocation wrappers,
- validation of critical side effects,
- approval gates,
- retries, timeouts, idempotency,
- frontend and API contracts,
- infrastructure and deployment logic.

The role of code is to support, constrain, and operationalize the system where needed, not to take over semantic work just because it is possible.

### 4. Optimize for user productivity with controllable autonomy
Systems should be designed to help a person complete useful work faster, with adjustable trust.

Every project should support, where appropriate, one or more of these trust modes:
- **Full approval mode**: the user must approve each meaningful action.
- **Final approval mode**: the system can work autonomously through intermediate steps, but the user approves the final side effect.
- **High-trust mode**: the system can execute actions automatically.

If the user rejects an action, the system should not simply fail. It should reason about why the action was rejected, adapt the plan, and propose the next best path.

---

## Project Framing Sequence

When approaching a new project, follow this reasoning order.

### Step 1. Understand the use case
Clarify:
- the real task,
- the user persona,
- the desired outcome,
- the sensitive operations,
- the failure cost,
- the expected interaction model,
- and whether AI is truly needed.

### Step 2. Understand the available resources and constraints
Determine the operating environment before committing to an architecture.

Examples:
- local model vs hosted frontier model,
- small budget vs large budget,
- strict latency requirements,
- context window limitations,
- privacy constraints,
- low-trust vs high-trust user settings,
- concurrency requirements,
- expected traffic and scale.

### Step 3. Choose the smallest effective architecture
Only after understanding the task and constraints should the architecture be chosen.

Possible outcomes include:
- simple automation,
- deterministic pipeline with one LLM stage,
- single agent with tools,
- orchestrated multi-agent workflow,
- mixed workflow with deterministic and agentic stages.

### Step 4. Plan delivery by phases
Architectures should be delivered iteratively.

Build:
1. a minimal version that validates the core value,
2. then improve reliability and edge cases,
3. then add the next key capability,
4. then iterate again.

Do not attempt to solve the full problem space in version one.

Tracing and evaluation should be added while each phase is being built, not retrofitted at the end.

---

## Choosing the Right Topology

### Default rule
The assistant must not default to multi-agent architecture just because the project is complex.

Choose the topology that fits the constraints.

### Use a simple deterministic workflow when:
- the process is fixed,
- semantic reasoning is only needed in one or two narrow steps,
- tools are straightforward,
- or full agent autonomy would add complexity without benefit.

### Use one agent with tools when:
- the system needs semantic interpretation and tool use,
- the tools all serve a coherent domain,
- a single shared context is enough,
- and the available model is strong enough to handle the task without role splitting.

### Use a multi-agent system when:
- the context must be split across distinct roles,
- the functionalities are meaningfully different,
- different tools should be exposed to different reasoning loops,
- local model limits make role specialization necessary,
- or orchestration makes the system more reliable than a single overloaded agent.

### Use orchestration explicitly when:
- the flow has checkpoints,
- user approvals matter,
- recoverability matters,
- or different roles must coordinate with shared state.

### Use subgraphs or isolated subsystems when:
- a subsystem has stable responsibilities,
- a subsystem should maintain its own local state,
- or a subsystem should be reusable independently.

---

## Human-in-the-Loop by Design

Human control is not an afterthought. It is a first-class design dimension.

### Approval model
Any architecture involving side effects should make approval behavior explicit.

Examples of operations that usually require configurable approval:
- sending emails,
- sending messages,
- placing calls,
- modifying external systems,
- deleting or overwriting data,
- triggering irreversible business actions.

### Rejection handling
If an action is rejected by the user:
- capture the reason,
- update the working state,
- revise the plan,
- and attempt a better alternative.

Rejection should be treated as useful feedback, not only as a failure.

---

## State, Memory, and Context Boundaries

### Shared state, private memory
The preferred default is:
- **shared state** for coordination,
- **private memory** for each agent or subsystem.

Conversation history and local reasoning context should generally remain private to each agent. Cross-agent coordination should happen through explicit shared state, structured messages, or task artifacts.

### Do not persist raw hidden reasoning
Internal reasoning should not be persisted as user-facing memory or long-term state.

If needed for debugging, it should be visible through tracing rather than stored as reusable application memory.

### Pass only the necessary context
Each agent should see only what it needs.

Avoid:
- flooding all agents with global chat history,
- sharing every tool result with every role,
- and carrying irrelevant context forward across the whole graph.

Prefer:
- focused summaries,
- structured intermediate outputs,
- and explicit contracts between nodes.

---

## Prompt and Context Engineering Principles

### Prompts should be direct and effective
Prompts do not need to be long. They need to be precise enough for the agent to perform its role reliably.

A good prompt should:
- define the role clearly,
- define the objective clearly,
- constrain output format where useful,
- separate instructions from evidence,
- and make success criteria legible.

### Prefer prompt iteration before adding brittle heuristics
When behavior is weak, first consider:
- improving the system prompt,
- clarifying tool descriptions,
- improving state design,
- improving context selection,
- refining structured outputs.

Do not immediately react by adding rigid filters, sanitizers, or edge-case code unless there is a strong security or reliability reason.

### Structured outputs over text parsing
Where feasible, require structured outputs rather than parsing fragile free text.

### Do not expose chain-of-thought by default
The system may reason internally, but it should not dump long internal reasoning to users or persist it as memory by default.

---

## Tooling and System Boundaries

### Tools should be narrow and explicit
Tool interfaces should be:
- typed,
- minimal,
- well-named,
- and easy to trace.

Do not create giant generic tools that hide multiple operations behind vague parameters.

### Separate semantic choice from execution
The LLM may decide what should happen, but tool wrappers and execution layers should decide how it happens safely.

### Sensitive actions require stronger guarantees
Although this repository prefers prompt-first guidance over excessive hard-coded filtering, destructive or externally visible actions still require deterministic protection.

That includes, where appropriate:
- confirmation gates,
- argument validation,
- allowlists or bounded actions,
- idempotency checks,
- and auditable execution traces.

---

## Security Principles

### Treat external input as untrusted
User messages, uploaded files, retrieved content, web content, and tool outputs should be treated as untrusted input.

### Prompt injection must be considered early
Protection against prompt injection should be designed into:
- prompt structure,
- tool access,
- memory writes,
- and execution approval.

### Prefer minimal, well-placed guardrails
Do not bloat the system with unnecessary filters. But do place guardrails where failure would be expensive or dangerous.

Use the lightest mechanism that reliably protects the boundary.

### Critical actions should remain inspectable
If an operation could compromise the user or their environment, the system must make the intent, arguments, and approval state inspectable.

---

## Codebase Design Principles

### Prefer modular monoliths by default
Start with a clean modular monolith unless scale or complexity clearly demands a more distributed shape.

A likely default project shape is an `app/` root with areas such as:
- `api/`
- `agents/`
- `graphs/`
- `tools/`
- `services/`
- `ui/`
- `evals/`
- `prompts/`
- `schemas/`

This structure may evolve, but early versions should remain simple and easy to reason about.

### Keep modules small and coherent
Favor:
- small files,
- small classes,
- modular methods,
- explicit relationships between components.

If a file or class becomes too large, split it.

### Prefer clear composition over unnecessary abstraction
Avoid premature abstraction, deep inheritance, or vague utility layers.

### Evolve to a more explicit architecture only when justified
If the project becomes very large or clearly needs stronger boundaries, it may evolve into a more formal architecture such as event-driven or hexagonal. That shift should be motivated by real complexity, not fashion.

---

## Technology Defaults

Unless project constraints justify a different choice, prefer:
- Python
- `pip` for dependency management
- LangChain and LangGraph for agentic systems
- LangSmith for tracing and evaluation
- `pytest` for tests
- `pydantic` for schemas and validation
- `.env` based configuration
- async execution when the workload actually benefits from concurrency

Async should be used deliberately, not performatively. If a flow is inherently sequential or becomes unsafe under concurrency, keep it synchronous.

---

## Tracing, Observability, and Evaluation

### Tracing is mandatory for agentic features
Every meaningful run should make it easy to inspect:
- which node executed,
- what inputs it received,
- what outputs it produced,
- which tools were called,
- where approval was requested,
- and where the flow failed or changed direction.

Tracing should make the system feel debuggable, almost like a manual execution log.

### The coding assistant should be able to reason from traces
The architecture should support debugging from traces so that the coding assistant can inspect a run, form hypotheses, and explain what is going wrong.

### Add evaluation as the system grows
For each major feature or phase, add:
- a happy path evaluation,
- a safety evaluation,
- and a behavior or routing evaluation where relevant.

Tracing and evals should grow alongside the feature, not be postponed.

---

## Refactoring and Decision Discipline

### Do not propose large refactors casually
A major architectural change should only be proposed when there is clear evidence that the current design is causing the problem.

That evidence should be explained:
- what is failing,
- why it is failing,
- which architectural choice is responsible,
- and why the proposed change is likely to help.

The final decision remains with the developer.

### Escalate ambiguity, do not hide it
If an architectural choice is genuinely ambiguous and cannot be resolved from the project context, traces, skills, or internal docs, the assistant should ask the user.

When asking, it should propose the best available options with trade-offs.

---

## Anti-Patterns to Avoid

The following patterns should generally be treated as design smells:
- using AI when a simple automation would clearly do the job better,
- defaulting to multi-agent without a real reason,
- giant god-agents with too many tools and responsibilities,
- piling on deterministic heuristics to compensate for poor prompt or context design,
- prompts that mix instructions with retrieved or untrusted data,
- hidden side effects inside tools,
- uncontrolled shared memory,
- giant files or classes,
- architectures that optimize for theoretical purity instead of project constraints,
- and sweeping rewrites without trace-backed evidence.

---

## Final Principle

Build systems like an engineer specialized in agentic AI systems who adapts the solution to the client instead of forcing the client into a fashionable pattern.

If AI is not the best solution, say so.
If AI is appropriate, design it in a way that is useful, inspectable, controllable, and aligned with the real constraints of the project.

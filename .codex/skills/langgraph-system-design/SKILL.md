---
name: langgraph-system-design
description: Use when implementing or refining an LLM-first workflow, agent, or multi-agent system in Python with LangGraph. Focus on explicit state design, graph topology, node responsibilities, subgraphs, persistence, approval-aware execution, and traceable orchestration.
---

# LangGraph System Design Skill

## Purpose

Use this skill when the task is to design or implement a system in LangGraph.

This includes:

- translating an approved architecture into a concrete LangGraph design
- deciding the graph structure for a workflow, agent, or multi-agent system
- defining state schemas
- deciding node boundaries and responsibilities
- choosing between fixed transitions, conditional edges, loops, parallelization, and subgraphs
- integrating human approval checkpoints
- designing persistence and resumability
- structuring the codebase so the graph stays modular and debuggable

This skill is for **system design and implementation structure inside LangGraph**, not for deciding whether LangGraph is needed in the first place. If that higher-level decision is still unresolved, use the architecture skill first.

## When to use

Use this skill when:

- the project will use LangGraph as the orchestration layer
- a single agent is no longer enough as an implementation structure
- the workflow needs explicit state, branching, or resumability
- the system includes approvals, retries, interruptions, or resumable execution
- multi-agent behavior needs to be implemented with clear role boundaries
- the assistant must decide how to map a workflow into state, nodes, and edges
- the user wants a production-grade structure rather than an ad hoc agent loop

## When not to use

Do not use this skill for:

- deciding whether AI is needed at all
- deciding whether the whole system should be multi-agent
- tiny single-function edits
- purely prompt-level work with no orchestration impact
- basic LangChain agent usage when no explicit graph design is needed

## Design stance

LangGraph should be used here as an explicit orchestration layer.

Do not treat it as a decorative wrapper around a giant agent.

The graph should make visible:

- what state exists
- which node owns which decision
- which transitions are deterministic
- where the model is allowed to reason
- where tools are called
- where approvals happen
- where retries or fallbacks happen
- where state is persisted
- how the workflow resumes after interruption or rejection

LangGraph should make the system easier to understand, not harder.

## Core LangGraph model

When using this skill, think in the native LangGraph model:

- **State** = the current snapshot of the workflow
- **Nodes** = the units of work that read state and return updates
- **Edges** = the transitions that determine what runs next

That is the basic mental model. Everything else should build on top of it.

Do not design nodes before designing state.
Do not design edges before understanding the decision boundaries.

## Default design order

When using this skill, reason in this order.

### Step 1. Define the workflow shape

Start by identifying the real execution shape:

- strict sequence
- sequence with approvals
- branching workflow
- loop until criteria is met
- orchestrator-worker pattern
- evaluator-optimizer loop
- one-agent tool loop inside a larger workflow
- multi-agent handoff or delegation
- map-reduce or parallel fan-out/fan-in

Do not start from code structure.
Start from workflow behavior.

### Step 2. Define the state schema

State is the backbone of the graph.

Before writing nodes, define:

- what the graph needs to know
- what changes across steps
- what must persist between interruptions
- what approvals or rejections must be remembered
- what artifacts must be passed across roles
- what belongs in shared state vs private subgraph state

Prefer explicit typed state.

Use:
- `TypedDict` for graph state schemas when appropriate
- Pydantic models for structured operational payloads and tool contracts
- clear field ownership and field meaning

State should be compact, intentional, and readable.

### Step 3. Define node responsibilities

Each node should have one clear job.

Good node responsibilities:
- route the request
- retrieve context
- draft a plan
- call the specialist subgraph
- prepare approval request
- wait for approval
- execute action
- handle rejection
- write validated memory artifact
- produce final response

Bad node responsibilities:
- route, retrieve, plan, call tools, and execute all inside one giant function
- hide multiple architectural decisions in one node
- mutate unrelated parts of state without clear ownership

### Step 4. Define transition logic

Once state and nodes are clear, define how execution moves.

Use explicit transition rules for:
- fixed steps
- conditional branches
- retries
- stop conditions
- rejection-aware alternative paths
- escalation or fallback
- parallel fan-out/fan-in when justified

Do not hide transitions inside nodes unless there is a very strong reason.

### Step 5. Add persistence and interruption behavior

If the system includes approvals, multi-turn work, resumability, or recovery, design persistence early.

Decide:
- what checkpointing strategy is needed
- what state must survive across runs
- what thread/session boundary exists
- where the system may pause
- how it resumes after approval, rejection, or failure

### Step 6. Add observability and evaluation hooks

Before implementation is considered complete, decide:
- which nodes need detailed tracing
- what metadata each step should emit
- which transitions are most failure-prone
- which evals should inspect final output
- which evals should inspect trajectory or node-level behavior

## State design rules

### Keep state explicit

A LangGraph workflow should not depend on hidden globals or vague in-memory side channels.

The graph state should make the workflow legible.

### Keep state purposeful

Every state field should answer one of these questions:
- what do we know?
- what do we need next?
- what decision was already taken?
- what did the user approve or reject?
- what artifact must survive to another node?

If a field does not serve a clear workflow purpose, it probably does not belong.

### Separate shared workflow state from local working memory

In multi-agent systems, not everything should live in shared state.

Use shared graph state for:
- stable cross-step facts
- routing decisions
- user constraints
- approval status
- artifacts that another node genuinely needs

Use local or subgraph-private state for:
- role-specific working context
- local message history
- temporary reasoning artifacts
- specialist-internal scratch information

Do not turn shared state into a dumping ground.

### Do not persist raw reasoning unnecessarily

Persist:
- decisions
- summaries
- approved artifacts
- validated outputs
- user feedback
- execution status

Avoid persisting:
- verbose internal chain-of-thought
- raw prompt fragments unless needed for debugging
- large irrelevant histories

## Node design rules

### One node, one responsibility

Each node should correspond to one meaningful step in the workflow.

If a node description needs “and then and then and then,” split it.

### Keep semantic work where semantics belong

Use model-powered nodes for:
- interpretation
- semantic routing
- drafting
- synthesis
- selecting among plausible paths

Use deterministic nodes for:
- explicit validation
- transport and integration concerns
- approval gating
- persistence
- state shaping
- external execution wrappers

### Make node input/output obvious from state

A node should read the minimal relevant state and return only the state updates it owns.

Do not have nodes mutate unrelated state fields casually.

### Avoid hidden subworkflows inside nodes

If a node performs multiple meaningful steps with branches or retries, consider turning that into:
- several nodes, or
- a subgraph

## Edge and control-flow rules

### Prefer explicit edges for meaningful decisions

If the workflow branches based on a meaningful condition, make that visible in the graph.

Examples:
- route to specialist A vs B
- request approval vs continue
- execute vs redraft
- retry vs fail
- user rejected vs approved

### Use loops intentionally

Loops are valid when the workflow genuinely iterates.

Examples:
- evaluator-optimizer refinement
- drafting until approval
- retrieve-critique-retry
- ask human / revise / retry

Every loop must define:
- entry condition
- exit condition
- maximum retries or stop logic when needed
- what changes across iterations

### Use parallelization only when it helps

Parallel fan-out/fan-in is useful when:
- tasks are independent
- latency matters
- state merging is clear
- concurrency will not corrupt meaning or correctness

Do not use parallelism just because it looks advanced.

## Subgraph rules

Use subgraphs when they make boundaries cleaner.

Typical reasons:
- a subsystem has its own private state
- a specialist agent should keep local memory separate
- a reusable workflow should be embedded in several parent graphs
- a team or module boundary needs a stable interface
- the parent graph should not care about internal specialist details

Subgraphs are especially useful in multi-agent systems where each specialist needs local context but the parent should only see the stable input/output contract.

Do not create subgraphs for trivial two-step logic.
Use them when they meaningfully improve modularity or isolation.

## Human-in-the-loop design rules

Approval behavior must be designed into the graph, not bolted on later.

### Model the trust mode explicitly

The graph should know whether the current run is:
- autonomous
- approval-for-side-effects
- approval-for-every-action

Do not rely on vague implicit behavior.

### Separate proposal from execution

Common pattern:
1. gather context
2. draft or decide
3. prepare approval artifact
4. wait for approval if needed
5. execute only after approval
6. handle rejection if rejected

This should be visible in the graph.

### Rejection is a transition, not an exception

If the user rejects an action, do not treat that as a generic error.

Model rejection as a legitimate branch that can:
- revise the plan
- choose another specialist
- redraft the action
- escalate for clarification
- terminate safely

## Persistence and checkpointing rules

Use persistence whenever the workflow needs:

- approvals
- long-running execution
- resumability
- fault tolerance
- multi-turn continuity
- thread-based state
- human interruption and later continuation

Design explicitly:
- what is checkpointed
- when it is checkpointed
- what identifies the thread/session
- what state fields are safe and useful to persist

Do not add persistence as an afterthought after the graph shape is already tangled.

## Tool integration rules inside LangGraph

LangGraph should orchestrate tool usage clearly.

Preferred pattern:
- a node decides whether a tool is needed
- tool calling happens visibly
- result comes back into state cleanly
- execution tools are approval-aware
- write and side-effect tools are not mixed casually with read tools

If one node is repeatedly deciding among many unrelated tools, ask whether:
- the toolset should be narrowed
- the role should be split
- a router or specialist subgraph is needed

## Model selection and deployment-aware design

Graph design must reflect model reality.

### When using weaker local models

Prefer:
- narrower prompts
- tighter role specialization
- more decomposition
- smaller context slices
- explicit routing
- more constrained node responsibilities

### When using stronger hosted models

You may prefer:
- fewer agents
- broader single-agent roles
- less decomposition
- faster end-to-end paths
- simpler orchestration

But do not assume a stronger model removes the need for approval design, tracing, or tool discipline.

## Code structure guidance

When implementing LangGraph systems in this repository, prefer a modular layout such as:

- `app/graphs/` for graph definitions and compilation
- `app/agents/` for role-specific agent logic or prompts
- `app/state/` for state schemas and related models
- `app/tools/` for tool contracts and implementations
- `app/services/` for integrations and external system adapters
- `app/prompts/` for prompt templates or builders
- `app/evals/` for datasets, evaluators, and experiment helpers
- `app/observability/` for tracing setup and shared metadata conventions

Do not put the whole graph, all nodes, all tools, and all prompts into one giant file.

If a graph or module grows too large, split by subgraph, role, or workflow phase.

## Required outputs when this skill is used

When responding under this skill, produce most of the following.

### 1. Graph shape

State the workflow pattern:
- sequence
- branching graph
- loop
- orchestrator-worker
- evaluator-optimizer
- subgraph-based multi-agent
- hybrid pattern

### 2. State schema proposal

List the key state fields and what each one means.

Identify:
- shared state
- private state if subgraphs are used
- persistent vs ephemeral fields

### 3. Node map

List each node and its job.

For each important node, state:
- what it reads
- what it writes
- whether it is semantic or deterministic
- whether it may call tools
- whether it requires approvals or emits approval artifacts

### 4. Transition map

State the key edges:
- fixed transitions
- conditional branches
- loops
- stop conditions
- rejection branches
- fallback branches

### 5. Persistence and interruption plan

State:
- whether checkpointing is required
- what survives across turns
- where the graph may pause
- how it resumes

### 6. Observability and eval priorities

State:
- what must be traced first
- what evals should exist early
- which nodes are likely to be high-risk or high-debug-value

## Anti-patterns to avoid

Avoid all of the following unless there is strong evidence otherwise:

- one giant LangGraph node that behaves like a hidden application
- state schemas that are vague bags of data
- shared state containing every local detail from every specialist
- using subgraphs for tiny logic that should just be nodes
- not using subgraphs when private local state is clearly needed
- approvals handled as ad hoc if-statements scattered through the code
- loops with no clear stop condition
- transitions hidden in opaque helper functions
- graph structure that mirrors files rather than workflow reality
- building nodes before deciding state ownership
- no checkpointing even though approvals or long pauses are required
- tracing added only after debugging becomes painful

## Documents to consult

When this skill is active, consult these repository documents if available:

- `docs/agent-engineering/architecture-principles.md`
- `docs/agent-engineering/prompting.md`
- `docs/agent-engineering/threat-model.md`
- `docs/agent-engineering/tool-contracts.md`
- `docs/agent-engineering/eval-strategy.md`
- `docs/agent-engineering/tracing-observability.md`

Use them to keep implementation aligned with architecture, security, tool discipline, evaluation, and observability expectations.

If the implementation depends on current framework behavior, search authoritative LangGraph or LangChain docs before finalizing the design.

## Output format

When responding under this skill, prefer this structure:

1. **Workflow pattern**
2. **State design**
3. **Node design**
4. **Transition design**
5. **Subgraphs and memory boundaries**
6. **Approval and execution flow**
7. **Persistence / checkpointing**
8. **Observability and eval hooks**
9. **Implementation structure**
10. **Main risks or likely failure points**

Keep the answer implementation-oriented.
Do not jump straight into code until the graph design is explicit.

## Final stance

This skill exists to ensure LangGraph is used as a real orchestration framework.

The graph should make system behavior explicit:
- what the system knows
- what each step does
- what the model decides
- what code guarantees
- when humans intervene
- what gets persisted
- how the system can be debugged later

If the graph does not make those things clearer, it is probably the wrong graph design.

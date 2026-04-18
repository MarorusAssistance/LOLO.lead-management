---
name: review-multiagent-topology
description: Use when reviewing, critiquing, or selecting a multi-agent topology for an LLM system in Python. Focus on deciding whether multi-agent is truly justified, choosing the right pattern, separating responsibilities and context, controlling tool access, and keeping the topology aligned with LangChain/LangGraph, approvals, and deployment constraints.
---

# Review Multi-Agent Topology Skill

## Purpose

Use this skill when the task is to decide whether a system should be multi-agent, or to review whether an existing multi-agent design is well structured.

This includes:

- deciding whether multi-agent is actually needed
- reviewing whether a single agent would be better
- choosing between common multi-agent patterns
- checking whether responsibilities are split in the right place
- checking whether context boundaries are clear
- checking whether tool exposure matches agent roles
- reviewing whether approvals and side effects are placed correctly
- reviewing whether the topology fits model quality, context limits, cost, and latency
- reviewing whether LangGraph subgraphs or explicit workflows would produce a cleaner design

This skill should stop the assistant from treating “multi-agent” as the default answer to any complex problem.

## When to use

Use this skill when:

- the user asks whether a use case should be multi-agent
- the system already uses multiple agents and needs design review
- one agent seems overloaded with too many roles or tools
- the current multi-agent topology feels overengineered
- context window pressure is a real constraint
- local-model limits are driving decomposition
- different roles need different tools, prompts, or memory
- the system includes orchestrator-worker, handoff, router, or specialist patterns
- the workflow includes human approvals or risky actions that may need role separation

## When not to use

Do not use this skill for:

- generic architecture decisions before deciding whether AI is needed at all
- tiny implementation changes inside an already accepted topology
- pure prompt work
- pure tool-contract work
- speculative “let’s add more agents” ideation with no concrete problem to solve

If the real question is whether the system should use AI in the first place, use the architecture skill first.

## Design stance

This repository does not assume that multi-agent is inherently better.

A single agent with the right prompt, tools, and context can often solve the problem more simply. LangChain’s current guidance says this explicitly: multi-agent systems are useful for context management, distributed development boundaries, or parallelization, but a single agent with the right prompt and tools can often achieve similar outcomes. citeturn214940search0

Use multi-agent only when it creates a real benefit such as:

- cleaner context isolation
- narrower specialist prompts
- safer tool separation
- better handling of context limits
- better parallelization
- cleaner team/module boundaries
- clearer approval and side-effect control

Do not use multi-agent just because the task is complex or because it sounds more advanced. LangChain’s docs stress that context engineering is central, and that many “multi-agent” requests are really about selective context and boundaries rather than agent count for its own sake. citeturn214940search0

## Core review questions

When this skill is active, review the topology by asking:

1. Why is multi-agent needed here at all?
2. What concrete problem is a single agent failing to solve?
3. Are the agents split by real responsibility, or by arbitrary implementation taste?
4. Does each agent have a clearly narrower context than a monolithic alternative?
5. Does each agent have a clearly narrower toolset than a monolithic alternative?
6. Are approvals and risky side effects separated from broad reasoning roles?
7. Does the topology fit the available model quality and context window?
8. Would a workflow with one semantic step or one agent be simpler and just as good?
9. Would LangGraph subgraphs or explicit workflows express the design more cleanly?
10. Is the system observable enough to justify the extra complexity?

## Default review order

When using this skill, reason in this order.

### Step 1. Test whether multi-agent is justified

Before accepting a multi-agent design, test the main reasons.

LangChain currently highlights three common reasons for multi-agent systems:
- context management
- distributed development / clear capability boundaries
- parallelization citeturn214940search0

A multi-agent topology is more defensible when at least one of these is real and load-bearing.

If none of them are strong, challenge the topology.

### Step 2. Compare against the strongest simpler alternative

Always compare the proposed topology against:
- simple automation
- workflow with one semantic step
- single agent with tools
- workflow with one agent inside it

Do not evaluate a multi-agent system in isolation.
Ask whether the same job could be done more cleanly with fewer moving parts.

### Step 3. Identify the real split dimension

A good agent split usually follows one or more of these dimensions:

- different domain knowledge
- different toolsets
- different context windows
- different risk / permission levels
- different interaction modes
- parallelizable subtasks
- reusable subsystem boundaries

A bad split usually sounds like:

- “one agent for analysis, one for thinking, one for execution” with no sharp boundaries
- role names that sound different but still require nearly the same context and tools
- arbitrary decomposition that just moves complexity around

### Step 4. Review context boundaries

LangChain emphasizes that context engineering is at the center of multi-agent design. citeturn214940search0

For each agent, inspect:
- what it sees
- what it does not see
- what memory it can read
- what memory it can write
- what artifacts it returns to the parent workflow
- whether that context is materially smaller or more relevant than a monolithic alternative

If every agent sees almost everything, the topology is probably weak.

### Step 5. Review tool boundaries

For each agent, inspect:
- which tools it can call
- whether the toolset is coherent for that role
- whether risky tools are isolated
- whether draft and execute tools are separated
- whether the agent still has too many unrelated tools

Multi-agent design should usually improve tool discipline, not make it fuzzier.

### Step 6. Review approval and side-effect boundaries

High-impact actions should usually sit behind narrower roles or explicit workflow checkpoints.

Inspect:
- whether execution authority is too broad
- whether one generalist agent can both reason widely and act destructively
- whether approval checkpoints are explicit
- whether user rejection has a clear path back through the topology

LangGraph is explicitly designed for durable execution and human-in-the-loop, which is relevant whenever approval-rich workflows are part of the design. citeturn214940search1turn214940search4

### Step 7. Review deployment fit

Topology must fit deployment reality.

Inspect:
- local vs hosted model
- context window limits
- latency constraints
- budget constraints
- concurrency opportunities
- whether weaker local models justify decomposition
- whether a stronger hosted model could simplify the topology

If the topology ignores these constraints, it is not production reasoning.

## Multi-agent patterns to recognize

LangChain’s docs describe several useful patterns for multi-agent systems, and the broader LangChain/LangGraph learn pages point to subagents, handoffs, routers, and skills-based patterns. citeturn214940search0turn214940search11

When reviewing a system, identify which pattern it is actually using.

### Pattern 1. Router + specialists

Use when:
- requests naturally divide into a bounded set of domains
- each domain has different context or tools
- only one specialist usually needs to handle a request at a time

Good sign:
- the router has a small, explicit action space
- each specialist is narrow
- routing quality can be evaluated clearly

Bad sign:
- the router is vague
- specialists overlap heavily
- specialists still need most of the same tools and context

### Pattern 2. Orchestrator-worker / supervisor-specialist

Use when:
- one coordinating role must plan or decompose work
- specialists perform bounded subtasks
- the parent role does not need the specialists’ full internal context
- work may be sequential or parallel

Good sign:
- specialists have clear contracts
- the orchestrator controls sequencing and aggregation
- shared state is intentional

Bad sign:
- supervisor is a god-agent
- specialists are hidden generalists
- subtask boundaries are not real

### Pattern 3. Handoffs

Use when:
- the active role should change based on state
- the conversation or task ownership moves from one role to another
- each stage has different responsibilities or permissions

Good sign:
- the active role is clear
- handoff criteria are explicit
- context transfer is controlled

Bad sign:
- handoffs are happening because prompts are weak, not because ownership really changes

### Pattern 4. Subgraph-based multi-agent

Use when:
- each specialist needs local private state
- a subsystem should hide internal complexity behind a stable input/output contract
- the parent workflow should only see the artifact returned, not the full internal reasoning

LangGraph’s subgraph model is valuable here because subgraphs help isolate private state and keep the parent graph cleaner. This fits especially well with specialist workflows and human-interruptible execution. citeturn214940search1turn214940search4turn214940search2

### Pattern 5. Parallel worker fan-out / fan-in

Use when:
- subtasks are independent
- latency matters
- outputs can be merged cleanly
- concurrency does not break meaning or correctness

Good sign:
- each worker has a bounded task
- merge logic is explicit

Bad sign:
- tasks are not truly independent
- the merge step is vague or overloaded

## Decision rules for agent count

### Prefer fewer agents when

- one role can handle the full task coherently
- toolsets are related
- context still fits
- a stronger model is available
- latency and simplicity matter more than decomposition
- the current agent count is mostly compensating for unclear prompts or weak tool design

### Prefer more agents when

- one role is clearly overloaded
- tasks are semantically different enough that prompts should be separate
- tools should be isolated by domain or risk
- context is too large for one role
- weaker local models benefit from narrower roles
- parallelization materially improves performance
- human approvals are easier to govern when separated from broad reasoning

## Review rules for memory and state

For each topology, inspect:

- what is shared state
- what is private memory
- whether shared state contains only stable cross-role artifacts
- whether local reasoning is isolated
- whether one agent can poison memory for unrelated agents
- whether rejection or approval signals are preserved across the topology

If every agent writes freely to the same shared memory, the topology is probably unsafe and hard to reason about.

## Review rules for approvals

Because this repository often uses human-in-the-loop modes, review:

- which agent proposes an action
- which agent or node requests approval
- which role can execute after approval
- whether the approval artifact is clear
- whether user rejection causes a meaningful topology change
- whether the system can search for an alternative path after rejection

LangGraph’s interrupt model is relevant here because it supports pausing execution for external input and resuming later, which is a strong fit for approval gates in agentic workflows. citeturn214940search4

## Required outputs when this skill is used

When responding under this skill, usually provide:

### 1. Is multi-agent justified?
State yes, no, or only conditionally.

### 2. What is the smallest viable topology?
Examples:
- single agent with tools
- router + specialists
- orchestrator + specialists
- handoff workflow
- subgraph-based specialist workflow

### 3. Why this topology fits
Explain using:
- context pressure
- tool isolation
- risk separation
- local vs hosted model constraints
- latency/cost constraints
- human approval needs

### 4. Why the main alternatives are worse
Contrast against:
- single agent
- workflow + one semantic step
- different multi-agent pattern
- stronger model + simpler topology

### 5. Agent boundary map
For each agent, state:
- role
- visible context
- tool access
- memory access
- approval authority
- output artifact

### 6. Main topology risks
Examples:
- router ambiguity
- supervisor overload
- duplicate context across agents
- too much shared memory
- too many tools per specialist
- no clean rejection path
- latency explosion from unnecessary agent count

### 7. What to trace and evaluate first
State:
- route quality
- tool-use quality
- approval compliance
- rejection handling
- latency/cost profile
- whether agents are actually reducing context burden

## Review checklist

When this skill is active, review the topology against these questions.

1. What real problem does multi-agent solve here?
2. Would a single agent be simpler and good enough?
3. Does each agent have a genuinely distinct role?
4. Does each agent have materially narrower context?
5. Does each agent have materially narrower tools?
6. Are risky actions isolated?
7. Are approvals explicit?
8. Is shared state disciplined?
9. Are subgraphs needed for local private state?
10. Does the topology fit the model and budget constraints?
11. Will traces make failures diagnosable?
12. Which evals would prove the topology is actually better?

## Anti-patterns to avoid

Avoid all of the following unless there is strong evidence otherwise:

- adding agents because the task “feels advanced”
- one supervisor with nearly all tools and nearly all context
- specialists that overlap heavily in role and tool access
- every agent seeing the full conversation and full system state
- multi-agent used to patch weak prompts or weak tool design
- no explicit approval boundaries in a risky workflow
- no distinction between proposal and execution roles
- no clean state ownership
- too many agents for a small or latency-sensitive task
- parallel workers where tasks are not truly independent
- hidden subagents inside tools instead of explicit topology

## Documents to consult

When this skill is active, consult these repository documents if available:

- `docs/agent-engineering/architecture-principles.md`
- `docs/agent-engineering/prompting.md`
- `docs/agent-engineering/threat-model.md`
- `docs/agent-engineering/tool-contracts.md`
- `docs/agent-engineering/eval-strategy.md`
- `docs/agent-engineering/tracing-observability.md`

Use them to keep the topology review aligned with architecture, prompting, security, tool discipline, evaluation, and observability.

If the recommendation depends on current framework behavior, consult authoritative LangChain/LangGraph docs before finalizing the review.

## Output format

When responding under this skill, prefer this structure:

1. **Is multi-agent justified?**
2. **Best-fit topology**
3. **Why this topology fits**
4. **Why the main alternatives are worse**
5. **Agent boundary map**
6. **Main risks or weaknesses**
7. **What to trace and evaluate first**

Do not just say “use multi-agent” or “do not use multi-agent.”
Make the trade-off explicit.

## Final stance

This skill exists to make multi-agent design harder to fake.

A good topology should solve a real context, capability, risk, or orchestration problem.
If it does not, it is probably just extra complexity.

The best multi-agent system is not the one with the most roles.
It is the one with the clearest boundaries, the cleanest context discipline, and the smallest amount of complexity needed to outperform simpler alternatives.

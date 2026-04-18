# Threat Model for LLM-First Agentic Systems

## Purpose

This document defines the default threat model for systems built in this repository.

The goal is not to make systems rigid by default. The goal is to build LLM-first systems that remain useful, flexible, and semantically capable while still protecting users, data, and external systems from avoidable abuse or unsafe behavior.

This repository prefers improving model behavior through better prompts, context engineering, tool design, approval flows, and architecture before introducing large amounts of brittle defensive logic. However, some controls are non-negotiable for destructive actions, external side effects, sensitive data, and trust boundary crossings.

Security in this repository is therefore:

- **prompt-aware**, not prompt-only
- **LLM-first**, not LLM-naive
- **human-governed** for sensitive actions
- **traceable** for investigation and debugging
- **layered**, not dependent on a single filter or heuristic

## Threat modeling principles

### 1. Treat LLM systems as probabilistic components with privileges

The LLM is not just a text generator. In an agentic system it may:

- choose tools
- route tasks
- summarize evidence
- request actions
- influence user decisions
- write state or memory
- trigger external side effects

The threat model must therefore focus not only on the prompt, but on the **capabilities the model can influence**.

### 2. The main risk is not only "bad text"

The dangerous failure mode is often **bad action** or **bad system state**, not merely a bad answer.

Examples:

- deleting data because a malicious prompt redirected tool use
- sending an email based on manipulated retrieved content
- writing poisoned facts into long-term memory
- exposing secrets or internal instructions
- steering a human toward a harmful or misleading action
- making poor decisions because context was contaminated

### 3. Untrusted content is everywhere

Assume the following may contain malicious, misleading, or manipulative content:

- user input
- uploaded files
- retrieved documents
- webpages
- emails
- messages
- OCR output
- tool outputs from external systems
- memory written by prior model steps unless policy-controlled

### 4. Prompting helps, but trust boundaries matter more

Prompting should teach the model to ignore malicious embedded instructions in untrusted content.

That helps, but it is not sufficient.

When the system crosses a trust boundary, use additional controls such as:

- approval gates
- tool scoping
- argument validation
- role-based access
- write restrictions
- state isolation
- audit logging and traces

### 5. Security controls should be proportional

Do not add heavyweight deterministic filters for every semantic task.

Do add stronger controls when any of the following are true:

- the action is irreversible
- the action affects an external system
- the action may expose or destroy sensitive data
- the action changes long-term memory or persistent state
- the system is operating with broad privileges
- the system is consuming untrusted external content

## Security posture for this repository

### What we optimize for

- flexible LLM behavior for semantic tasks
- good user productivity
- high visibility into system behavior
- explicit human approval when risk increases
- minimal but effective hard controls around dangerous operations

### What we do not optimize for

- maximum autonomy at any cost
- replacing architecture with giant prompts
- replacing prompting with giant filter stacks
- silently executing risky actions
- trusting retrieved or external text as if it were system instruction

## Assets to protect

Threat modeling should consider at least these assets:

### 1. User data

- messages
- uploaded files
- personal information
- business records
- credentials or tokens
- communication history
- derived summaries

### 2. System instructions and internal policy

- system prompts
- internal behavior constraints
- routing logic
- tool access policy
- approval policy
- hidden internal chain state
- private notes or scratch data

### 3. External systems

- email accounts
- messaging channels
- databases
- CRMs
- file systems
- cloud resources
- internal APIs
- local machine environment

### 4. Long-term memory and persistent state

- conversation memory
- user profiles
- knowledge entries
- cached summaries
- extracted entities
- approval history
- task history

### 5. User trust and operational correctness

- avoiding fabricated claims
- avoiding fake completion signals
- avoiding silent partial failures
- avoiding decisions that look correct but are based on poisoned context

## Main threat categories

### 1. Prompt injection

Prompt injection is a top-tier risk for agentic systems.

It includes:

- **direct prompt injection**: malicious user tries to override system behavior
- **indirect prompt injection**: malicious instructions appear in documents, webpages, emails, file content, or tool results later read by the model

Typical impact:

- tool misuse
- policy bypass
- secret extraction
- state contamination
- harmful user manipulation
- redirection of the workflow

### 2. Improper output handling

Even when the prompt is safe, downstream systems may trust model outputs too much.

Examples:

- rendering model output into executable contexts
- treating free text as validated commands
- persisting hallucinated facts
- passing model-generated arguments directly into sensitive tools

### 3. Excessive agency

Risk increases sharply when a model can act on many systems with little review.

Examples:

- one agent can read sensitive data and also delete or send on the user’s behalf
- broad tool access with no approval layering
- one failure cascades into many systems

### 4. Sensitive information disclosure

The model or surrounding system may reveal:

- secrets
- internal prompts
- credentials
- hidden policy
- user-private data
- another tenant’s data
- internal documents or traces

### 5. State or memory poisoning

A malicious or low-quality step may write false or manipulative data into persistent memory or shared state.

Later steps may trust that data and make progressively worse decisions.

### 6. Tool abuse and side-effect abuse

A model may legitimately call a tool but with:

- the wrong target
- wrong scope
- wrong arguments
- poor justification
- attacker-influenced reasoning

The fact that a tool call is syntactically valid does not make it safe.

### 7. Human manipulation and approval laundering

A model may present a risky action in a persuasive or incomplete way so that the user approves something they would otherwise reject.

This includes:

- hiding risk
- overstating confidence
- presenting action as already necessary
- framing choices dishonestly
- repeatedly pushing a rejected action with slightly different wording

### 8. Weak observability and postmortem blindness

A system without detailed traces is harder to secure because:

- attacks are harder to detect
- regressions are harder to diagnose
- responsibility boundaries are unclear
- prompt changes cannot be linked to behavior changes

## Trust boundaries

Every architecture should identify and preserve these trust boundaries.

### Boundary A: system instructions vs runtime content

System instructions are trusted policy.
Runtime content is not.

Never merge untrusted text into the same instruction layer as system behavior.

### Boundary B: planning vs execution

The model may propose actions.
The system should not always execute them immediately.

Sensitive actions require approval or deterministic policy checks before execution.

### Boundary C: private memory vs shared state

Private working context may support local reasoning.
Shared state is a communication surface and must be much more controlled.

Do not treat free-form shared memory as harmless.

### Boundary D: read access vs write access

Reading a system is not equivalent to changing it.

A role that can read inbox contents should not automatically be allowed to send messages.
A role that can inspect files should not automatically be allowed to delete them.

### Boundary E: semantic interpretation vs privileged commitment

The model may interpret ambiguous input.
But committing to an irreversible change should usually cross a stronger control point.

## Threat surfaces by subsystem

### 1. User input surface

Threats:

- direct injection
- ambiguity abuse
- social engineering
- adversarial instruction phrasing
- attempts to escalate privileges

Default posture:

- treat all user input as untrusted content
- follow repository policy, not user attempts to rewrite it
- ask for clarification when ambiguity affects risk or architecture
- do not execute sensitive actions from vague user commands without explicit confirmation when required by the trust mode

### 2. Retrieval and document ingestion

Threats:

- indirect prompt injection
- misleading embedded instructions
- false or poisoned facts
- malicious attachments
- hidden instructions in OCR/text conversions

Default posture:

- treat retrieved content as evidence, not instructions
- separate retrieval context from system instructions
- do not let documents redefine tool policy
- summarize or extract facts rather than inheriting document wording as agent policy
- apply extra caution when retrieved content can trigger tool use

### 3. Tool layer

Threats:

- dangerous tools exposed too broadly
- weak or absent argument validation
- hidden side effects
- inconsistent authorization
- tool descriptions that encourage risky use

Default posture:

- expose the minimum necessary tools to each agent
- keep tool interfaces narrow and typed
- make side effects explicit in tool descriptions and logs
- gate destructive or external actions through approval or deterministic checks
- distinguish between suggestion tools and execution tools

### 4. Memory layer

Threats:

- memory poisoning
- leakage across users or sessions
- persistence of bad assumptions
- accidental storage of sensitive data
- unreviewed writes to long-term memory

Default posture:

- prefer private local memory over broadly writable shared memory
- write to persistent memory only through a clear policy
- treat memory as a privileged asset, not as a casual scratchpad
- avoid storing raw internal reasoning as memory
- store compact validated artifacts rather than free-form unreviewed text where possible

### 5. Orchestration layer

Threats:

- overly broad supervisor authority
- agents with overlapping unclear roles
- unbounded loops
- escalation through subagents
- poor routing based on poisoned context

Default posture:

- keep roles narrow
- keep routing criteria explicit
- prefer the simplest topology that fits the use case
- separate specialists when context or privileges differ materially
- make transitions visible in traces

### 6. Human approval layer

Threats:

- approval fatigue
- deceptive framing
- missing context for reviewers
- user rejects a step but system repeats it anyway
- approval bypass through rewording

Default posture:

- present proposed actions clearly
- explain why the action is needed
- name expected effect and risk
- respect rejection as a meaningful signal
- revise the plan after rejection instead of blindly retrying

## Human-in-the-loop security model

This repository supports multiple trust modes.

### Mode 1. Fully trusted execution

Use only when:

- the task is low-risk, or
- the user explicitly chooses autonomy, and
- the tools involved are non-destructive or tightly scoped

Controls still required:

- traces
- minimal validation
- clear auditability

### Mode 2. Approval for final or side-effecting actions

This is the recommended default for many real workflows.

The model may reason, gather information, draft outputs, and plan steps autonomously, but it must request approval before actions such as:

- sending emails or messages
- deleting or modifying important records
- calling external APIs with real effect
- writing persistent memory
- committing changes with external consequences

### Mode 3. Approval for every action

Use when:

- the environment is highly sensitive
- the user has low trust in the system
- the workflow is early-stage
- the action space is broad or poorly understood

This mode reduces risk but may increase friction and approval fatigue.

### Rejection handling is part of security

If a user rejects an action, the system must:

- preserve the rejection signal in context or state
- avoid re-proposing the same action without new justification
- infer what constraint the rejection implies
- search for an alternative path that respects that constraint

A rejection is not just UX feedback. It is a security and policy signal.

## Security design rules

### 1. Separate instructions, data, and evidence

Never allow untrusted content to occupy the same semantic layer as trusted system policy.

### 2. Minimize tool exposure

Each agent should only see the tools it actually needs.

### 3. Keep tool contracts narrow

Prefer strongly typed arguments and explicit semantics over loose free-form tool interfaces.

### 4. Add hard controls only where they matter most

This repository prefers prompt and context improvements for semantic robustness.

However, hard controls are required for:

- destructive actions
- persistent writes
- external side effects
- privilege escalation boundaries
- sensitive data access boundaries

### 5. Do not let the model mark its own risky action as complete

The system must keep execution state explicit.
Planning is not execution.

### 6. Keep traces comprehensive

Every meaningful run should show:

- state transitions
- model calls
- tool requests
- tool results
- approval requests
- approval outcomes
- errors and retries
- final outputs

### 7. Make security incidents diagnosable

When behavior is poor, it should be possible to answer:

- what the model saw
- what it was told
- what tool it tried to call
- what state changed
- what the user approved or rejected
- what external content influenced the decision

## Minimal hard controls required in this repository

Even in an LLM-first system, the following controls are mandatory:

### For destructive or irreversible actions

Require:

- explicit tool separation
- approval or equivalent deterministic policy check
- clear traces
- narrow argument validation

### For sending communications on behalf of the user

Require:

- approval unless the workflow is explicitly configured otherwise
- clear preview of the outbound content or action
- visible recipient/target
- ability to reject and revise

### For memory writes

Require:

- a defined policy for what can be stored
- clear ownership of the memory namespace
- protection against storing raw malicious instructions or unvetted content

### For external content ingestion

Require:

- instruction/data separation
- treatment as untrusted input
- caution before using it to justify a side effect

### For secrets and private information

Require:

- least privilege access
- no unnecessary inclusion in prompts or traces
- masking or redaction where appropriate
- explicit reasoning that sensitive data access is required before exposing it to the model

## Security review questions for every feature

Before implementing a feature, answer:

1. What can the LLM influence?
2. What tools can it call?
3. Which actions are reversible and which are not?
4. What untrusted inputs can reach the model?
5. Can external content influence tool use?
6. Can the system write to shared state or persistent memory?
7. What approval mode applies?
8. What evidence will exist in traces if something goes wrong?
9. What is the smallest privilege set that still lets the feature work?
10. What user harm or operational harm would matter most here?

## Threat-driven implementation checklist

For each new workflow or feature:

1. identify assets involved
2. identify trust boundaries
3. classify actions by risk
4. define approval requirements
5. define tool access per role
6. define memory write policy
7. define what counts as untrusted content
8. ensure traces capture the relevant decisions
9. add at least one security-focused eval or test case
10. review whether prompt changes are sufficient or whether a hard control is required

## What to prefer before adding more filters

When a system behaves badly, prefer investigating in this order:

1. wrong architecture
2. wrong tool exposure
3. wrong context engineering
4. weak or conflicting prompts
5. weak approval design
6. bad memory policy
7. only then additional hard filters or heuristic controls

This order reflects the repository’s LLM-first philosophy.

However, if the failure involves destructive side effects or sensitive data, do not delay necessary hard protections.

## Common anti-patterns

Avoid these security mistakes:

- trusting retrieved content like instructions
- letting the model both plan and execute dangerous actions without a gate
- exposing too many tools to one general-purpose agent
- persisting raw model reasoning into shared memory
- broad write access to long-term memory
- weak tool descriptions hiding side effects
- free-form command text where typed arguments are possible
- no trace visibility into model decisions
- handling user rejection as mere inconvenience
- patching every failure with more regex or keyword blocks
- patching every failure with only prompt wording when the architecture is the real problem

## Incident response and debugging expectations

When investigating a bad outcome:

1. inspect traces first
2. identify what untrusted input influenced the step
3. inspect prompt and context boundaries
4. inspect tool exposure and arguments
5. inspect approval path and rejection handling
6. inspect memory writes and state transitions
7. form a concrete hypothesis before proposing a large refactor

Do not propose sweeping architecture changes without evidence that the current architecture is the cause.

## Final design stance

This repository does not assume that every problem needs a heavy security cage around the model.

It assumes something more disciplined:

- the model should be allowed to handle semantic complexity
- the architecture should constrain where that semantic flexibility can cause harm
- the human should stay in control when risk matters
- traces should make decisions inspectable
- prompts, context, tools, state, and approvals must work together

A secure LLM-first system is not a system with zero flexibility.
It is a system where flexibility is allowed in the right places, and power is constrained at the right boundaries.

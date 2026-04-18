# Tool Contracts for LLM-First Agentic Systems

## Purpose

This document defines how tools must be designed, exposed, validated, and reviewed in this repository.

In this repository, tools are not random helper functions. They are the boundary between model-driven reasoning and real system behavior.

A good tool contract should make the LLM more effective without turning the system into a brittle code-first workflow. The contract should be strong enough to keep actions understandable, traceable, and safe, but not so rigid that the model loses useful semantic flexibility.

## Why tool contracts matter

In an LLM-first system, the model often decides:

- whether a tool should be used
- which tool should be used
- when it should be used
- with what arguments
- how to interpret the result

That means tool quality directly affects system quality.

Poor tool contracts usually cause one or more of these failures:

- the wrong tool gets selected
- the right tool is called with vague or malformed arguments
- the model overuses tools for tasks it could solve itself
- side effects happen without enough clarity
- traces become hard to interpret
- prompt injection can influence tool usage too easily

Tool contracts are therefore one of the main control surfaces in an agentic system.

## Design stance for this repository

This repository is LLM-first, not tool-naive.

That means:

- semantic decision-making should often stay with the model
- tool contracts should support model reasoning, not replace it
- tools should be narrow, explicit, and understandable
- destructive or external actions should still cross stronger control points
- approval layers and validation should sit around tools where risk matters

We do **not** want to compensate for weak prompting or weak architecture by creating huge tool APIs with excessive hidden logic.

We also do **not** want free-form tool interfaces so loose that the model can do almost anything with unclear consequences.

## Definition of a tool

A tool is a callable capability exposed to an agent.

A good repository-level tool contract defines at least:

- the tool's purpose
- what problem it solves
- what inputs it accepts
- what inputs are required
- what inputs are optional
- what outputs it returns
- whether it has side effects
- what permissions or approval level it requires
- what failures can happen
- what traces should be recorded

## Core principles

### 1. Tools must be single-purpose and narrow

A tool should do one thing clearly.

Bad:
- `manage_customer_data`
- `handle_email_workflow`
- `process_document`

Better:
- `get_customer_record`
- `update_customer_status`
- `draft_email_reply`
- `send_email`
- `extract_invoice_fields`

If a tool does too many things, the model has to infer hidden branches inside the tool instead of making an explicit choice at the orchestration level.

That usually harms traceability and control.

### 2. Separate read tools from write tools

Read actions and write actions should not be blurred together.

Examples:

- `search_messages` should be separate from `send_message`
- `get_file_contents` should be separate from `delete_file`
- `draft_email` should be separate from `send_email`

This improves:

- approval design
- security review
- trace clarity
- routing behavior
- rejection handling

### 3. Make side effects explicit

Every tool must clearly state whether it is:

- read-only
- state-modifying
- externally side-effecting
- destructive or irreversible

Do not hide side effects behind generic names.

Bad:
- `process_task`
- `sync_data`
- `finalize_item`

Better:
- `delete_record`
- `send_email`
- `create_calendar_event`
- `write_memory_entry`

The model, the developer, and the reviewer should all understand the impact of the tool from its name and schema.

### 4. Prefer typed inputs and outputs

Tool arguments should be explicit and typed.

Avoid vague free-form blobs when a structured schema would do.

Prefer:

- Pydantic models
- explicit enums
- bounded strings
- optional fields only when they are truly optional
- lists with clear semantics
- booleans only when their meaning is obvious

Bad argument design creates bad agent behavior.

### 5. Keep the semantic decision outside the tool when possible

Do not push semantic reasoning into tool internals if it belongs in the model or orchestration layer.

Bad pattern:
- tool receives a vague user request and internally performs hidden classification, routing, extraction, retries, and execution

Preferred pattern:
- the model decides the task
- the model chooses the tool
- the tool performs a well-scoped operation
- the result comes back clearly

A tool should not become a hidden mini-agent unless that abstraction is intentional and visible.

### 6. Tool descriptions are part of the prompt

The description is not just documentation.

It shapes:

- whether the tool gets called
- how often it gets called
- what arguments the model tries to provide
- whether the model understands the scope correctly

Tool descriptions should be:

- specific
- concise
- behaviorally informative
- honest about constraints
- explicit about side effects

### 7. Tool outputs should be useful for the next step

A tool result should make the next decision easier.

Prefer outputs that are:

- structured
- compact
- unambiguous
- easy to trace
- easy to pass into the next node or UI layer

Avoid returning giant raw payloads unless the next step genuinely needs them.

## Tool taxonomy for this repository

Every tool should be classified into one of these categories.

### 1. Read tools

Purpose:
- fetch data
- search
- inspect
- retrieve
- preview
- validate availability

Examples:
- `search_customer_notes`
- `get_invoice`
- `list_projects`
- `preview_email_draft`

Default posture:
- usually safer
- often callable with less friction
- still subject to least privilege
- may still require caution if sensitive data is involved

### 2. Analysis tools

Purpose:
- transform or compute without external side effects
- run deterministic utilities
- format, compare, summarize, parse, or score data

Examples:
- `compare_versions`
- `normalize_contacts`
- `calculate_quote`
- `convert_markdown_to_html`

Default posture:
- safe if they do not mutate state
- should remain clearly deterministic
- should not silently call external services unless declared

### 3. Drafting tools

Purpose:
- create proposed artifacts without executing external actions

Examples:
- `draft_email`
- `prepare_update_message`
- `build_followup_summary`

Default posture:
- useful for human-in-the-loop flows
- should be separated from execution tools
- often pair well with approval modes

### 4. Execution tools

Purpose:
- perform real actions in external or persistent systems

Examples:
- `send_email`
- `create_ticket`
- `update_crm_record`
- `delete_document`
- `write_memory_entry`

Default posture:
- higher scrutiny
- explicit approval policy
- tighter validation
- strong trace visibility

### 5. Orchestration-support tools

Purpose:
- assist workflow state, coordination, or controlled delegation

Examples:
- `store_intermediate_result`
- `request_human_approval`
- `enqueue_followup_task`

Use carefully.
These should not become a substitute for proper graph/state design.

## Required fields in a tool contract

Every production-grade tool in this repository should define the following.

### 1. Tool name

The name should be:
- action-oriented
- specific
- unambiguous

Good examples:
- `search_customer_records`
- `draft_sales_email`
- `send_sales_email`
- `delete_invoice`

Bad examples:
- `assistant_tool`
- `do_task`
- `workflow_handler`
- `process_data`

### 2. Tool description

The description should answer:

- what the tool does
- when it should be used
- when it should not be used
- what kind of input it expects
- whether it causes side effects
- whether approval may be required

Example:

> Use this tool to draft an email reply based on the current conversation and context. This tool creates a proposed draft only and does not send anything.

That is much better than:

> Handles email tasks.

### 3. Input schema

The input schema must be explicit.

It should define:

- field names
- field meaning
- field types
- allowed values where relevant
- optional vs required fields
- any constraints that matter to correctness

Prefer narrow schemas over giant generic dictionaries.

### 4. Output contract

The output should define:

- what shape is returned
- whether the call succeeded
- what result data is available
- whether a retry is appropriate
- whether the result is final or only a proposal

The output should support both humans and downstream code.

### 5. Side-effect classification

Every tool must declare one of these:

- read-only
- local state change
- persistent state change
- external side effect
- destructive / irreversible

If the classification is not obvious, the tool is underspecified.

### 6. Approval requirement

Each tool should define whether it is:

- always safe to run automatically
- safe only in certain trust modes
- always approval-gated
- never directly runnable by a general-purpose agent

### 7. Failure modes

The contract should define realistic failure modes, for example:

- not found
- unauthorized
- invalid input
- rate limited
- dependency unavailable
- ambiguous target
- side effect rejected
- human approval rejected

This improves both prompting and debugging.

### 8. Trace expectations

The tool contract should specify what must be visible in traces:

- tool name
- arguments or safely redacted arguments
- caller node or agent
- approval state
- result summary
- errors
- retries
- external target if relevant
- whether side effects happened

## Input design rules

### Prefer explicit fields over free-form text

Bad:
```json
{ "request": "do whatever is needed for this customer" }
```

Better:
```json
{
  "customer_id": "cust_123",
  "objective": "prepare_followup",
  "channel": "email"
}
```

Free-form text is sometimes necessary, but it should not be the default for operational actions.

### Keep schemas minimal

Do not add ten optional fields just in case.

A smaller schema:

- is easier for the model to fill correctly
- is easier to validate
- is easier to trace
- is easier to evolve

### Use enums where the action space is truly bounded

Enums are useful when the set of valid choices is stable and meaningful.

Examples:
- approval mode
- message channel
- sort order
- action type

Do not use enums when the space is naturally open-ended.

### Validate what matters operationally

This repository does not want to overfit safety with giant filter layers.

Still, operationally meaningful validation is required for:

- IDs
- required targets
- action type consistency
- presence of approval tokens when needed
- fields that could accidentally point to the wrong external target

Validation should protect execution integrity, not attempt to replace semantic reasoning.

## Output design rules

### Return structured results

Prefer outputs such as:

- `success`
- `status`
- `result`
- `error`
- `requires_followup`
- `preview`
- `execution_metadata`

This makes orchestration cleaner.

### Distinguish proposal from execution

A drafting tool should return a proposal.
An execution tool should return whether execution happened.

Never blur the two.

Bad:
- returning a generated email body from a tool that also sent the email without making that obvious

Good:
- `draft_email` returns `draft`
- `send_email` returns `sent: true`, recipient, message_id, timestamp, or a failure reason

### Keep outputs compact but sufficient

Do not return full raw upstream API payloads by default unless debugging or downstream logic requires them.

Prefer normalized outputs that preserve what matters.

## Approval-aware tool design

Because this repository heavily uses human-in-the-loop patterns, tools should be designed with approval in mind.

### Separate proposal from commit

A common pattern should be:

1. inspect or gather
2. draft or propose
3. request approval if required
4. execute
5. trace result

This is better than one tool that gathers, decides, and executes all at once.

### Respect rejection as a first-class outcome

If a user rejects an action, that should not be treated as a generic failure.

The system should preserve:

- what action was proposed
- why it was rejected
- what alternative paths remain

Tooling should make this easy to represent.

### Trust mode must influence tool availability or execution policy

The current trust mode should determine whether:

- the tool is callable at all
- the tool pauses for approval
- the tool can run only after a prior draft step
- the tool can execute autonomously

## Security rules for tools

### 1. Treat tool-call arguments as high-impact data

Even if the model generated the arguments, they should not be assumed correct.

### 2. Do not expose broad dangerous tools unnecessarily

Avoid tools like:
- `run_sql`
- `call_any_api`
- `execute_shell_command`
- `modify_any_record`

unless the environment, user, and scope clearly justify them.

Even then, prefer constrained wrappers.

### 3. Prevent hidden escalation

A read tool should not secretly trigger writes.
A preview tool should not secretly send.
A helper tool should not secretly call external systems.

### 4. Keep external content untrusted

If a tool consumes data from documents, web pages, or messages, its contract should not imply that this content becomes trusted instruction.

### 5. Use stronger controls for dangerous tools

Destructive or sensitive tools require:

- narrower input schemas
- explicit approval behavior
- clearer descriptions
- stronger trace visibility
- more disciplined tests and evals

## Async and concurrency guidance

This repository often uses async flows, but async should be intentional.

Use async when:
- the tool performs I/O
- multiple calls can run concurrently without corrupting state
- latency matters
- the graph or workflow supports safe concurrency

Do not use async blindly when:
- the step must remain sequential for correctness
- shared mutable state would race
- external ordering matters
- approval flow depends on strict step order

Tool contracts should make concurrency assumptions clear where relevant.

## Tool composition rules

### Avoid god-tools

A tool should not internally perform:

- retrieval
- classification
- planning
- drafting
- execution
- post-processing
- memory writes

all in one hidden block.

That is not a tool contract. That is an untraceable workflow.

### Prefer composable steps

A better pattern is:

- `search_relevant_records`
- `draft_response`
- `request_approval`
- `send_response`

This preserves model flexibility and human control.

### Use subagents intentionally, not accidentally

If a capability really needs internal multi-step reasoning, make that architectural choice explicit.

Do not disguise an internal agent as a simple utility tool.

## Tool contract review checklist

Before accepting a tool into the system, review:

1. Is the tool single-purpose?
2. Is the name explicit?
3. Is the description behaviorally clear?
4. Is the schema typed and narrow?
5. Are read and write concerns separated?
6. Are side effects explicit?
7. Is approval behavior defined?
8. Are failure modes documented?
9. Will traces make the call understandable later?
10. Could this tool be split into safer smaller tools?
11. Is the tool solving the right layer of the problem?
12. Is semantic reasoning staying outside the tool where appropriate?

## Anti-patterns

Avoid these tool design mistakes:

- one tool doing many unrelated operations
- tool names that hide real side effects
- giant `dict[str, Any]` inputs with undocumented semantics
- output that forces brittle parsing
- one tool that both drafts and sends
- one tool that reads and writes with no separation
- tool wrappers that silently call multiple external systems
- broad admin-style tools exposed to general-purpose agents
- putting architecture logic inside tools
- using tools to patch poor prompts instead of fixing prompts or context
- using tools to patch poor architecture instead of fixing orchestration or state design

## Example contract template

```python
from pydantic import BaseModel, Field
from typing import Literal, Optional


class DraftEmailInput(BaseModel):
    recipient: str = Field(description="Email recipient address")
    subject: str = Field(description="Proposed subject line")
    purpose: str = Field(description="Goal of the email")
    tone: Literal["formal", "neutral", "friendly"] = Field(
        description="Desired communication tone"
    )
    context_summary: Optional[str] = Field(
        default=None,
        description="Short relevant context to incorporate into the draft"
    )


class DraftEmailOutput(BaseModel):
    success: bool
    draft_subject: str
    draft_body: str
    requires_human_review: bool = True
    status: Literal["draft_created", "failed"]
    error: Optional[str] = None
```

Contract notes:
- single purpose
- drafting only
- no hidden send action
- explicit schema
- easy to trace
- easy to plug into approval flow

## Repository default recommendations

By default, tools in this repository should aim for the following:

- one clear capability per tool
- typed Pydantic inputs and structured outputs
- separate read, draft, and execute phases
- explicit side-effect classification
- approval-aware behavior for risky actions
- no hidden routing or hidden agent loops inside tools
- high trace visibility
- minimal but meaningful validation
- composability with LangGraph stateful workflows
- compatibility with human-in-the-loop execution

## Final design stance

In this repository, tools are the execution boundary.

They should be:

- narrow enough to control
- expressive enough to be useful
- typed enough to be reliable
- transparent enough to debug
- safe enough to trust in production
- simple enough that the model can actually use them correctly

A strong tool contract does not make the system more deterministic than necessary.
It makes the system more legible, composable, and governable while preserving the parts that should remain LLM-driven.

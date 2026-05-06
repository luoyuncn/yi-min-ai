# Tool Visibility Routing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce hallucination pressure by removing duplicated tool descriptions from the system context and only exposing a route-specific subset of tools to the main model call.

**Architecture:** Add a lightweight pre-routing model call that classifies the current turn into a small set of tool domains, then filter `ToolRegistry` visibility by route before building the main `LLMRequest`. Keep the skill index in context, but stop injecting the human-readable tool index because the function schemas already carry that information.

**Tech Stack:** Python, pytest, existing `AgentCore` / `ToolRegistry` infrastructure

---

### Task 1: Lock the target behavior with tests

**Files:**
- Modify: `tests/core/test_context.py`
- Modify: `tests/tools/test_registry.py`
- Modify: `tests/core/test_loop.py`
- Modify: `tests/core/test_fitness_flow.py`
- Modify: `tests/core/test_loop_events.py`

**Step 1: Write failing tests**

- Assert that `ContextAssembler` keeps `[技能索引]` but no longer injects `[工具索引]`.
- Assert that `ToolRegistry` can filter visible tools by route tags.
- Assert that `AgentCore` performs a routing call before the main model request and that the main request only sees the route-specific tool subset.
- Update expectations for flows that now include an extra router call.

**Step 2: Run targeted tests to verify they fail**

Run: `python -m pytest tests/core/test_context.py tests/tools/test_registry.py tests/core/test_loop.py -q`

**Step 3: Confirm failures match the missing routing/visibility behavior**

- Missing route-aware tool filtering
- Context still includes tool index
- Core loop still makes only one main model request

### Task 2: Add route-aware tool visibility

**Files:**
- Modify: `agent/tools/models.py`
- Modify: `agent/tools/registry.py`

**Step 1: Extend tool metadata**

- Add `visibility_tags` to `ToolDefinition`.

**Step 2: Add registry filtering**

- Teach `ToolRegistry.names()`, `get_schemas()`, and `get_index()` to optionally filter by visibility tags.

**Step 3: Assign route tags**

- Mark tools as `always`, `general`, `fitness`, `bookkeeping`, `notes`, `scheduling`, `current_events`, or `identity`.

**Step 4: Re-run registry tests**

Run: `python -m pytest tests/tools/test_registry.py -q`

### Task 3: Insert a lightweight router before the main loop

**Files:**
- Modify: `agent/core/loop.py`

**Step 1: Add router prompt and parser**

- Build a no-tool routing request that returns one route label.
- Parse only known labels and fall back to `general`.

**Step 2: Filter main-call tools**

- Route before `ContextAssembler.assemble()`.
- Pass only visible tools into `_run_loop()`.

**Step 3: Preserve resume/approval behavior**

- Ensure `_resume_from_command()` still calls `_run_loop()` with an explicit tool set.

**Step 4: Re-run core loop tests**

Run: `python -m pytest tests/core/test_loop.py tests/core/test_loop_events.py tests/core/test_fitness_flow.py -q`

### Task 4: Remove duplicated tool descriptions from context

**Files:**
- Modify: `agent/core/context.py`
- Modify: `agent/app.py`

**Step 1: Stop injecting the text tool index**

- Keep the `skill_index` block in the system message.
- Remove the `[工具索引]` block.

**Step 2: Adjust system prompt wording**

- Refer to “当前回合真正可见的工具” instead of `[工具索引]`.

**Step 3: Re-run context and prompt-adjacent tests**

Run: `python -m pytest tests/core/test_context.py tests/test_agent_main.py -q`

### Task 5: Final verification

**Files:**
- No new files

**Step 1: Run the focused verification suite**

Run: `python -m pytest tests/core/test_context.py tests/tools/test_registry.py tests/core/test_loop.py tests/core/test_loop_events.py tests/core/test_fitness_flow.py tests/test_agent_main.py -q`

**Step 2: Record known gaps**

- `tests/test_app.py` requires `litellm` in the environment; if unavailable, note that this suite was not run.

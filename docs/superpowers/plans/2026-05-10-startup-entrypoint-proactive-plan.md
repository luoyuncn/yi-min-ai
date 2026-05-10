# Startup Entrypoint Proactive Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `agent.main --mode gateway` and `agent.main --mode all` start proactive scheduling by default.

**Architecture:** Reuse the existing `ProactiveScheduler` and mirror the already-working `agent.gateway.main` wiring inside `agent.main`. Keep Web-only and CLI testing behavior unchanged, and update README so `agent.main` is the recommended entrypoint.

**Tech Stack:** Python asyncio, Click CLI, pytest, existing scheduler/gateway modules.

---

### Task 1: Test Proactive Wiring

**Files:**
- Create: `tests/test_main_proactive.py`

- [ ] **Step 1: Write failing tests**

Create tests that monkeypatch `agent.main` dependencies and assert `_run_gateway()` and `_run_all()` start `ProactiveScheduler`, while `enable_proactive=False` skips it.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_main_proactive.py -q`

Expected before implementation: failures showing `_run_gateway()` / `_run_all()` do not accept or start proactive.

### Task 2: Wire Proactive in `agent.main`

**Files:**
- Modify: `agent/main.py`

- [ ] **Step 1: Add CLI switch**

Add `--enable-proactive/--no-proactive`, defaulting to enabled, and pass it into `_run_gateway()` and `_run_all()`.

- [ ] **Step 2: Start and stop scheduler**

Import `ProactiveScheduler`, instantiate it from `settings.proactive`, start it when enabled, and stop it in `finally`.

- [ ] **Step 3: Set gateway service early**

Assign each runtime app's `core.runtime_services.gateway = gateway` before schedulers can use tools that depend on it.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_main_proactive.py -q`

Expected: pass.

### Task 3: Update README and Verify

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Simplify startup guidance**

Make `uv run python -m agent.main --mode all` the full default command and de-emphasize `agent.gateway.main`.

- [ ] **Step 2: Run regression tests**

Run: `uv run pytest tests/test_main_proactive.py tests/scheduler/test_proactive.py tests/config/test_loader.py -q`

Expected: pass.

- [ ] **Step 3: Commit and push**

Commit only this work and push the current branch to its upstream remote.

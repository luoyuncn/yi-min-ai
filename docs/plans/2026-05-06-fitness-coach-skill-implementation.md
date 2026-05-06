# Fitness Coach Skill Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a default workspace-local `fitness-coach` skill package that scaffolds into new workspaces and provides the first usable professional-coach-plus-light-RPG fitness skill.

**Architecture:** The implementation keeps the existing loader and tool system unchanged. We add a new default skill template under `agent/skills/defaults/fitness-coach/`, extend the existing workspace scaffolding test to expect it, and vendor the content files that define the skill, profile templates, world templates, and references.

**Tech Stack:** Python, pytest, workspace-local markdown skill templates

---

### Task 1: Extend scaffolding coverage for the new fitness skill

**Files:**
- Modify: `tests/test_app.py`
- Test: `tests/test_app.py`

**Step 1: Write the failing test**

Extend `test_build_app_scaffolds_default_workspace_skills` to assert that:

- `workspace/skills/fitness-coach/SKILL.md` exists
- `workspace/skills/fitness-coach/profiles/EXAMPLE-FITNESS-LOG.md` exists
- `workspace/skills/fitness-coach/worlds/default/WORLD-LOG.md` exists
- the scaffolded skill text contains `fitness-coach`
- the skill text contains `专业、鼓励、数据驱动的个人健身教练`
- the skill text contains `剧情模式`
- the default world text contains `风痕原野`

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py::test_build_app_scaffolds_default_workspace_skills -v`

Expected: FAIL because `fitness-coach` has not been added to the default skills template directory.

**Step 3: Write minimal implementation**

Do not change production Python code yet. The failure should prove that only template content is missing.

**Step 4: Run test to verify it still fails for the expected reason**

Run the same targeted test and confirm the missing-file failure is the reason.

**Step 5: Commit**

Do not commit yet. Continue to Task 2.

### Task 2: Add the default `fitness-coach` skill package

**Files:**
- Create: `agent/skills/defaults/fitness-coach/SKILL.md`
- Create: `agent/skills/defaults/fitness-coach/profiles/EXAMPLE-FITNESS-LOG.md`
- Create: `agent/skills/defaults/fitness-coach/profiles/EXAMPLE-STORY-LOG.md`
- Create: `agent/skills/defaults/fitness-coach/profiles/EXAMPLE-WORLD-LOG.md`
- Create: `agent/skills/defaults/fitness-coach/references/coach-guide.md`
- Create: `agent/skills/defaults/fitness-coach/references/default-world-guide.md`
- Create: `agent/skills/defaults/fitness-coach/references/narrative-templates.md`
- Create: `agent/skills/defaults/fitness-coach/references/rpg-themes.md`
- Create: `agent/skills/defaults/fitness-coach/worlds/default/WORLD-LOG.md`
- Create: `agent/skills/defaults/fitness-coach/worlds/default/STORY-LOG.md`

**Step 1: Write minimal content**

Create the first version with:

- one skill definition that clearly scopes activation to fitness topics
- one profile template that separates training profile and workout history
- one story template that stores chapter state and RPG settings
- one world template for the `风痕原野` default world
- concise reference docs for coach style, default world, narrative mapping, and title/level themes

**Step 2: Run the targeted test to verify it passes**

Run: `uv run pytest tests/test_app.py::test_build_app_scaffolds_default_workspace_skills -v`

Expected: PASS

**Step 3: Run related regression tests**

Run:

- `uv run pytest tests/skills/test_skill_loader.py tests/tools/test_registry.py -v`

Expected: PASS

**Step 4: Refine for clarity**

Keep the content concise and domain-scoped. Do not add new runtime logic yet.

**Step 5: Commit**

```bash
git add tests/test_app.py agent/skills/defaults/fitness-coach
git commit -m "feat: add default fitness coach skill"
```

### Task 3: Verify the skill package against the design contract

**Files:**
- Check: `docs/plans/2026-05-06-fitness-coach-skill-design.md`
- Check: `agent/skills/defaults/fitness-coach/**`

**Step 1: Compare content against the design**

Confirm the packaged skill covers:

- professional coach first
- light RPG default on
- dialogue-based settings updates
- four configuration groups
- original Zelda-like but non-infringing default world

**Step 2: Run focused verification**

Run:

- `uv run pytest tests/test_app.py::test_build_app_scaffolds_default_workspace_skills tests/skills/test_skill_loader.py tests/tools/test_registry.py -v`

Expected: PASS

**Step 3: Commit if needed**

If content changes were required:

```bash
git add agent/skills/defaults/fitness-coach
git commit -m "docs: refine fitness coach skill templates"
```


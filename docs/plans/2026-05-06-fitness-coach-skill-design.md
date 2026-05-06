# Fitness Coach Skill Design

**Date:** 2026-05-06  
**Status:** Approved for implementation  
**Decision:** Add a workspace-local `fitness-coach` skill that remains domain-scoped to fitness conversations, preserves the current agent architecture, and defaults to a professional coach plus light RPG mode.

---

## Context

The current project already has the right host capabilities for a long-running domain skill:

- workspace-local skills under `workspace/skills/`
- skill indexing plus on-demand `read_skill`
- workspace-scoped file tools
- long-term memory, notes, session archive, reminders, and scheduler support

That means the fitness coach feature should not be built as a new runtime or a global persona override. It should be built as an independent skill package that the agent reads only when the conversation is clearly fitness-related.

The reference project `fitness-coach-rpg` is useful because it proves a strong structure:

- `FITNESS-LOG` for real training data
- `STORY-LOG` for dynamic narrative state
- `WORLD-LOG` for stable worldbuilding

We will preserve that separation, but adapt it to this repository's local skill model and safety boundaries.

---

## Product Direction

The target experience is:

- professional, safe, data-driven fitness coaching first
- light RPG flavor on by default
- strong continuity across weeks and months
- user-configurable coaching style
- user-configurable world and narrative density
- fitness-only activation, with no spillover into unrelated conversations

The user explicitly chose:

- `professional coach + light RPG` as the default mode
- a default world with strong `Breath of the Wild / Tears of the Kingdom` style atmosphere
- not a direct franchise copy
- an original world that keeps the same exploration mood while avoiding protected names and one-to-one lore reuse
- the protagonist is the real user transported into the world, not a separate fictional hero

---

## Scope Boundary

The fitness coach skill must remain independent from the rest of the assistant.

### Fitness-domain activation

The skill should be read and followed when the user expresses fitness intent such as:

- start training
- initialize profile
- what should I train today
- log today's workout
- help me recover after a break
- adjust my split
- explain this movement
- continue the story after training

### Non-fitness conversations

Outside fitness topics, the agent should continue to use its normal core behavior. The fitness skill should not become a persistent global overlay for unrelated topics like reminders, bookkeeping, note search, general chat, or news.

### Relationship with existing subsystems

Existing modules may support the skill, but not take ownership away from it:

- memory may store stable preferences and constraints
- notes may store explicit long-term fitness facts if requested
- session archive may help with lookback
- reminders may support training reminders

But:

- training logic stays in the fitness skill
- coaching decisions are not delegated to generic memory alone
- unrelated modules must not silently rewrite the fitness profile

---

## Configuration Model

The skill configuration is organized into four groups.

### A. Training Profile

This group directly affects training planning, exercise choice, progression, and recovery handling.

Fields:

- `name`
- `age`
- `height_cm`
- `weight_kg`
- `goal`
- `level`
- `equipment`
- `schedule`
- `preferred_time`
- `injuries`
- `plan_style`
- `movement_restrictions`
- `current_program_notes`

Interpretation:

- `goal`, `level`, `equipment`, `injuries`, and `plan_style` are high-priority planning inputs
- current-day fatigue, soreness, and readiness are not permanent profile facts; they are session-level state

### B. Coaching Style

This group changes how the agent coaches, without changing its safety baseline.

Fields:

- `primary_coach`
- `coach_mix_rules`
- `tone_style`
- `explanation_depth`
- `encouragement_level`
- `interaction_mode`

Examples:

- strength days follow Wang/Tan style
- cardio days follow a more Pamela-like structure
- recovery days are calmer and technique-focused
- concise coaching vs detailed movement explanation

### C. Light RPG Settings

This group controls the optional fun layer.

Fields:

- `rpg_enabled`
- `rpg_theme`
- `story_density`
- `pre_battle_narration`
- `post_battle_narration`
- `attribute_display`
- `title_style`

Defaults:

- RPG enabled
- low story density
- short pre-training scene
- short post-training scene
- visible level and title progression

### D. World and Story Settings

This group controls the exploration layer and long-term narrative continuity.

Fields:

- `world_mode`
- `world_name`
- `protagonist_mode`
- `identity_role`
- `core_drive`
- `npc_seed`
- `location_seed`
- `main_hook`
- `canon_locked_facts`

Defaults:

- use the built-in original exploration world
- protagonist is the real user in the other world
- light narrative, exploration-first, no forced epic destiny framing

---

## Modification Rules

The skill must support changing settings through normal dialogue after initialization.

### Temporary preferences

These should take effect immediately without rewriting the core profile:

- no story today
- keep it light this week
- be more encouraging today
- just give the plan, no long explanation
- lower intensity today

These are session-level or short-term preferences.

### Formal settings changes

These should require explicit confirmation before writing files:

- change long-term goal
- change coach style mix
- record a new injury constraint
- permanently lower or raise narrative density
- switch the world theme
- revise training split

The agent should restate the intended change before persisting it.

### Stable facts that should not be casually overwritten

These require explicit correction workflows:

- historical workout results
- completed training sessions
- established injury history
- locked story events
- previously confirmed world canon

If the user wants to revise history, the assistant should treat it as a correction rather than an ordinary preference update.

---

## File Layout

The skill should live under:

```text
workspace/skills/fitness-coach/
```

Recommended structure:

```text
fitness-coach/
├── SKILL.md
├── profiles/
│   ├── EXAMPLE-FITNESS-LOG.md
│   ├── EXAMPLE-STORY-LOG.md
│   └── EXAMPLE-WORLD-LOG.md
├── references/
│   ├── coach-guide.md
│   ├── default-world-guide.md
│   ├── rpg-themes.md
│   └── narrative-templates.md
└── worlds/
    └── default/
        ├── WORLD-LOG.md
        └── STORY-LOG.md
```

### Proposed ownership

- `FITNESS-LOG.md` stores real training profile, current program, and historical sessions
- `STORY-LOG.md` stores dynamic chapter state, quests, unlocked points, and current narrative state
- `WORLD-LOG.md` stores world rules, locations, NPCs, and stable canon

### Settings placement

To keep updates simple, v1 should include dedicated settings sections near the top of each log:

- profile and training settings in `FITNESS-LOG.md`
- RPG display and narrative density in `STORY-LOG.md`
- world mode and exploration lore in `WORLD-LOG.md`

This keeps the configuration human-readable and editable through conversation.

---

## Default World Direction

The default world should be an original setting that strongly evokes bright, open-air exploration adventure without directly reusing franchise-specific names or lore.

### Atmosphere

The atmosphere is:

- healing
- bright
- windy
- spacious
- exploratory
- quietly mysterious rather than oppressive

This is not grimdark fantasy. Even ruins and ancient machinery should feel like invitations to keep going, not like constant doom.

### World premise

The default world name is:

- `风痕原野`

It is a wide open land shaped by high wind corridors, ancient relay structures, grasslands, broken bridges, elevated towers, forgotten shrines, and dormant resonance devices.

The protagonist is the real user transported into this world. The user's real physical condition still matters. Growth is therefore grounded in actual training consistency rather than fictional destiny shortcuts.

### Power system

The world's power system is:

- `回响`

Ancient structures respond to rhythm, balance, breath, force production, and bodily control. Training improves the user's capacity to resonate with these devices.

Movement mapping:

- push days -> opening barriers, driving mechanisms, forceful activation
- pull days -> climbing, retrieving, drawing back, controlled extraction
- legs days -> long travel, tower ascent, burden carrying, landing stability
- recovery days -> camp restoration, spring tuning, breathing calibration

### Starting locations

#### 1. 风岬营地

The first stable safe point. It supports planning, post-workout reflection, recovery, and preparation.

#### 2. 晴脊草原

A bright grassland with broken gates, transport rails, movable mechanisms, and open traversal space. Best suited for push and pull mappings.

#### 3. 浮风塔群

A cluster of climbable towers and wind-linked platforms. Best suited for legs, balance, endurance, and progression through vertical exploration.

### Core NPCs

#### 岚枝

Guide figure focused on pacing, recovery, terrain sense, and calm judgment.

#### 砾舟

A practical, strength-oriented mechanic and mover who maps well to technical lifting cues and force production.

#### 逐光

A light, optimistic route-tracker who turns progress into exploration rewards and discovery momentum.

### Main hook

The world-level hook is:

- the resonance network of `风痕原野` is slowly falling out of alignment

This should create long-term direction without turning the story into a constant apocalypse narrative.

---

## Level System

The skill should include a visible `Lv.1-100` progression model.

### Stage bands

- `Lv.1-10 苏醒期`
- `Lv.11-30 旅者期`
- `Lv.31-60 共鸣期`
- `Lv.61-80 远征期`
- `Lv.81-100 苍穹期`

### Design intent

Levels are not just combat numbers. They represent how far the user can travel, what trials they can stabilize, and how well their body can handle the world's resonance.

### Experience sources

- completing a planned training session
- sustaining consistency across sessions
- PRs or meaningful technique improvement
- intelligent comeback after a break
- sensible recovery management

### Anti-patterns

The system should avoid rewarding reckless behavior:

- grinding through poor form at very high fatigue should not produce inflated rewards
- fatigue and repeated poor recovery can reduce story quality or progression speed

### Titles

Titles should feel adventurous, not overly theatrical:

- `初醒者`
- `风途旅者`
- `高地行者`
- `回响攀登者`
- `天穹远征者`

---

## Initialization Flow

The first-time setup should gather only the minimum information needed to begin safely and enjoyably:

1. basic body stats and training background
2. goal and available equipment
3. injuries and movement restrictions
4. schedule and preferred training timing
5. coach style preferences and mix rules
6. confirmation that light RPG mode is enabled by default
7. confirmation that the default world is `风痕原野` unless the user wants another theme

The system should not force the user to fully design the world before training can begin.

---

## Runtime Behavior

### Before training

The skill should:

- read the user's fitness profile
- check recent workout history
- assess comeback and recovery status
- generate a plan aligned to goal, equipment, and injuries
- optionally show a short scene-setting line if RPG is enabled

### During training

The skill should:

- give set-by-set guidance where appropriate
- adapt intensity based on live feedback
- prioritize technique and safety
- keep RPG text short and non-disruptive

### After training

The skill should:

- log objective results
- capture subjective feedback
- update progression
- append a short narrative reflection
- preserve continuity for the next session

---

## Integration Strategy

This skill should be introduced as a workspace-local skill first, not as a code-heavy subsystem rewrite.

### v1 integration

- vendor a local `fitness-coach` skill package under `workspace/skills/`
- rely on existing `read_skill`, `file_read`, and `file_write`
- let the model initialize and update the logs through the current tool system

### v2 opportunities

If the experience proves valuable, later implementation may add:

- structured workout storage beyond markdown
- dedicated workout tools
- recovery summaries
- training reminder presets
- web UI panels for levels, history, and current chapter

These are enhancements, not prerequisites for the first useful version.

---

## Open Implementation Notes

The design intentionally keeps the first version file-driven because:

- it matches the reference skill's strengths
- it fits the current repository architecture
- it allows direct user-controlled editing and inspection
- it avoids premature schema lock-in

The most important implementation rule is this:

The agent must treat the fitness skill as a specialized domain module, not as a replacement for the assistant's global identity or all-purpose behavior.


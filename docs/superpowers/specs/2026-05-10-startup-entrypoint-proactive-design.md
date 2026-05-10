# Startup Entrypoint and Proactive Scheduler Design

## Context

The project currently exposes two gateway-style startup paths:

- `uv run python -m agent.main --mode gateway`
- `uv run python -m agent.main --mode all`
- `uv run python -m agent.gateway.main`

Only `agent.gateway.main` wires `ProactiveScheduler`. The documented full app command, `uv run python -m agent.main --mode all`, starts Web plus Gateway-related schedulers but does not start proactive idle cycles. This makes the "agent has free time and may message the user" feature appear enabled in docs/config while never running for the user's normal command.

## Goal

Make `agent.main` the trusted startup entrypoint. In the full runtime modes, all user-facing background capabilities should be enabled by default:

- Feishu gateway
- Web UI in `--mode all`
- Heartbeat
- Cron
- Reminder
- Proactive idle messaging

The user should not need to know about a second gateway module to get proactive behavior.

## Non-Goals

- Redesigning `ProactiveScheduler` behavior or prompt wording.
- Changing Feishu adapter behavior.
- Changing memory extraction, note storage, or session archive behavior.
- Removing development-only modes such as CLI testing or Web-only startup.

## Proposed Behavior

`agent.main --mode gateway` should start Feishu plus all schedulers by default, including proactive.

`agent.main --mode all` should start Web UI, Feishu, and all schedulers by default, including proactive.

`agent.main --mode web` should remain Web-only.

`agent.main --mode cli --testing` should remain a local testing path.

`agent.gateway.main` should no longer be presented as the recommended command. It can remain as a compatibility path for now, but README should steer users toward `agent.main`.

## Configuration

Add a CLI switch to `agent.main`:

- `--enable-proactive/--no-proactive`
- default: enabled

The switch is still gated by `config/agent.yaml`:

- if `proactive.enabled: true`, the scheduler starts;
- if `proactive.enabled: false`, startup logs that proactive was skipped.

Use the existing proactive config fields:

- `min_interval_minutes`
- `max_interval_minutes`
- `quiet_hours`
- `session_id`
- `channel`
- `channel_instance`

## Architecture

Reuse the existing `ProactiveScheduler` from `agent.scheduler.proactive`.

In `_run_gateway()` and `_run_all()`:

1. Build apps via `build_channel_apps_async()`.
2. Create `GatewayServer`.
3. Register runtime apps and Feishu adapters.
4. Assign `runtime_services.gateway = gateway` before any scheduler or tool path that may call `message_send`.
5. Start Heartbeat, Cron, Reminder, and Proactive when enabled.
6. Stop all started schedulers in `finally`.

Single-channel configs should enable proactive. Multi-runtime configs remain conservative: if there is more than one runtime instance, disable fanout schedulers until routing policy is explicitly designed.

## Data Flow

The proactive loop follows the existing flow:

1. `ProactiveScheduler` sleeps for a random configured interval.
2. It skips quiet hours.
3. It creates an internal `NormalizedMessage` with `sender="proactive"` and `session_id="__proactive__"`.
4. `AgentCore` decides whether to return content or `[不打扰]`.
5. If content is returned, `GatewayServer.send_to_channel()` sends it to the configured session or the most recent non-internal session.

## Error Handling

Startup should log and continue if proactive startup fails, matching the current `agent.gateway.main` behavior.

Runtime send failures should continue to be handled by `GatewayServer.send_to_channel()` and the Feishu adapter. The proactive scheduler should log failures without killing the whole gateway.

If no target session can be resolved and no explicit `proactive.session_id` is configured, proactive should skip sending and log the existing warning.

## Testing

Add focused tests that do not call real Feishu or real providers:

- `agent.main --mode gateway` wires `ProactiveScheduler` when CLI and config allow it.
- `agent.main --mode all` wires `ProactiveScheduler` when CLI and config allow it.
- `--no-proactive` prevents proactive startup.
- `runtime_services.gateway` is set before tools/schedulers rely on it.

Existing scheduler tests cover `ProactiveScheduler` send/skip behavior and should remain unchanged.

## Documentation

Update README startup guidance to make `agent.main` the single recommended entrypoint:

- full production: `uv run python -m agent.main --mode all`
- gateway-only: `uv run python -m agent.main --mode gateway`
- local CLI testing: `uv run python -m agent.main --mode cli --testing`
- Web-only development: `uv run python -m agent.main --mode web`

Remove or de-emphasize `uv run python -m agent.gateway.main` from the main quick-start list.

## Acceptance Criteria

- Running `uv run python -m agent.main --mode all` starts proactive scheduling when config enables it.
- Running `uv run python -m agent.main --mode gateway` starts proactive scheduling when config enables it.
- Running either command with `--no-proactive` does not start proactive scheduling.
- README no longer implies that `agent.gateway.main` is the only command with proactive behavior.
- Tests pass for the changed startup and scheduler wiring paths.

# GPT-6 Work platform integration

Status: **implemented and locally testable; not installed in ChatGPT and not a
live model migration**.

The additive skill at `.agents/skills/gpt6-work-platform/` provides a reusable
orchestration contract plus a dependency-free read-only runtime kernel. It is
intended for research, coding, files and tool-heavy work, not only finance.

Read the skill's `SKILL.md` and `references/DEPLOYMENT.md` for exact behavior,
trust assumptions, commands, integration points and rollout/rollback procedure.

## Repository validation

```sh
python3 -m unittest discover -s .agents/skills/gpt6-work-platform/tests -v
python3 .agents/skills/gpt6-work-platform/scripts/smoke.py
```

No paid API calls, external writes, account changes or transactions occur. Fixture
tests must never be reported as actual GPT-6/baseline comparisons.

## ChatGPT Work installation

This repository is source code, not a global ChatGPT settings interface. Package
the `gpt6-work-platform` folder including its SKILL.md, scripts, references and
tests. In a ChatGPT surface that exposes Skills, use Plugins > Skills > Create >
Upload from your computer, then verify installation and a read-only invocation.
Availability and administrative permission must be checked in the actual account.
Do not assume installation synchronizes between desktop and web/mobile.

A repository checkout can expose the `.agents/skills/` directory to supporting
coding-agent hosts. That still does not install it into ChatGPT's account-wide
Skill catalog. The skill must be invoked/read by the host to affect a task.

## Unchanged boundaries

The existing Factory runtime, queue, model invocation path, `config.json`,
50-worker Swarm configuration, economic switches, external VPS/OpenClaw services,
Runbook and scheduled tasks are unchanged. The new maximum-three concurrency
limit applies only to this new read executor. There is no automatic migration
from a 50-worker Swarm and no provider-native async implementation.

The production deploy workflow runs on pushes to main/master. Keep this change
on a review branch until the deployment consequence is reviewed. The additional
CI workflow runs offline tests only and has read-only contents permission.

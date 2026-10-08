# ctfdeploy

Deploys a challenge repo to a CTF host: Docker Swarm stacks for hosted challenges and CTFd
challenges, visible from the moment their event starts. Desired state is a function of the repo
at `main` and the clock, so one idempotent command, `ctfdeploy reconcile`, converges the host.

## Commands

| Command | Where | Does |
| --- | --- | --- |
| `check [REPO]` | CI, host | Validates `ctfs.yml` and every challenge that is up or to come |
| `changed BASE [REPO]` | CI | Lists hosted challenges that differ from `BASE`, as JSON |
| `build DIR`, `up DIR`, `probe DIR`, `solve DIR` | CI, locally | Tests one challenge on a local swarm |
| `reconcile [--fetch] [REPO]` | Host | Deploys, syncs CTFd, removes what is no longer wanted |

`--github` before the command prints GitHub Actions groups, annotations and a summary; it is on
by default inside Actions. `reconcile` reads `CTFD_TOKEN`, `CTFD_URL` (default
`unix:/run/ctfd/ctfd.sock`) and, optionally, `DISCORD_WEBHOOK` with `STATE_DIRECTORY`.

Exit codes: 0 converged, 1 some challenges failed, 2 the run could not start.

## In a challenge repo

```yaml
# .github/workflows/challenges.yml
name: challenges
on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:
jobs:
  challenges:
    uses: utisss/ctfdeploy/.github/workflows/challenges.yml@<tag>
    with:
      host: ctf.isss.io
```

The GitHub environment named after the host holds the `DEPLOY_SSH_KEY` secret and the
`KNOWN_HOSTS` variable.

## Development

```bash
pixi run -e dev test
```

```bash
pixi run -e dev lint
```

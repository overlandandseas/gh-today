# gh-today

A terminal dashboard that shows today's commits on any GitHub repo, with PR links, JIRA tickets, and GitHub Actions workflow statuses.

`cd` into any git repo, run `gh today`, and see what happened today.

## Features

- Auto-detects the current GitHub repo from your working directory
- Shows commits, authors, PR numbers, JIRA ticket IDs, and workflow statuses
- Interactive — press Enter on any cell to open URLs, copy SHAs, etc.
- Per-repo configuration via a YAML config file (similar to [gh-dash](https://github.com/dlvhdr/gh-dash))
- Lazy-loads workflow statuses with spinner animations

## Prerequisites

- [`gh`](https://cli.github.com) CLI, installed and authenticated (`gh auth login`)
- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)

## Installation

**As a gh extension (recommended):**

```sh
gh extension install overlandandseas/gh-today
```

**From source:**

```sh
git clone https://github.com/overlandandseas/gh-today.git
cd gh-today
gh extension install .
```

## Usage

```sh
gh today              # show today's commits
gh today --yesterday  # show yesterday's commits
```

### Interactive keys

| Column   | Action on Enter                |
| -------- | ------------------------------ |
| SHA      | Copy full SHA to clipboard     |
| Author   | Open GitHub profile in browser |
| PR #     | Open pull request in browser   |
| Jira ID  | Open JIRA ticket in browser    |
| Workflow | Open workflow run in browser   |

Press `q` to quit.

## Configuration

Create a config file at `~/.config/gh-today/config.yml`:

```yaml
# Global JIRA URL (used as default for all repos)
jira_url: https://mycompany.atlassian.net

# Default settings for repos not explicitly listed
defaults:
  branch: main
  action_names: []

# Per-repo overrides
repos:
  my-org/my-app:
    action_names:
      - ci.yml
      - deploy.yml

  my-org/other-repo:
    branch: master
    action_names:
      - test.yml
    jira_url: https://other.atlassian.net
```

### Resolution order

Values are resolved per-repo, most specific wins:

| Setting        | 1st                           | 2nd                    | Fallback   |
| -------------- | ----------------------------- | ---------------------- | ---------- |
| `branch`       | `repos.<repo>.branch`         | `defaults.branch`      | `main`     |
| `action_names` | `repos.<repo>.action_names`   | `defaults.action_names`| `[]`       |
| `jira_url`     | `repos.<repo>.jira_url`       | top-level `jira_url`   | disabled   |

### Config file location

The config file is resolved in this order:

1. `$GH_TODAY_CONFIG` environment variable (exact path)
2. `$XDG_CONFIG_HOME/gh-today/config.yml`
3. `~/.config/gh-today/config.yml`

The app works with no config file — it just won't show workflow columns or JIRA links.

## License

[WTFPL](LICENSE)

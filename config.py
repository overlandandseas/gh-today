"""Configuration loading and repo auto-detection for gh-today.

Config file resolution order:
  1. $GH_TODAY_CONFIG env var (exact path)
  2. $XDG_CONFIG_HOME/gh-today/config.yml
  3. ~/.config/gh-today/config.yml

Config values are resolved per-repo with this precedence:
  repo-specific override > defaults section > hardcoded fallback
"""

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class RepoConfig:
    """Resolved configuration for a single repo."""

    repo: str  # "owner/name"
    branch: str = "main"
    action_names: list[str] = field(default_factory=list)
    jira_url: str = ""
    jira_projects: list[str] = field(default_factory=list)


_HARDCODED_DEFAULTS = {
    "branch": "main",
    "action_names": [],
    "jira_url": "",
    "jira_projects": [],
}


def detect_repo() -> str:
    """Auto-detect the current GitHub repo via the gh CLI.

    Returns the "owner/name" string (e.g. "my-org/my-repo").
    Exits with an error message if detection fails.
    """
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            capture_output=True,
            text=True,
            check=True,
        )
        repo = result.stdout.strip()
        if "/" not in repo:
            print(f"error: unexpected repo format from gh: {repo!r}", file=sys.stderr)
            sys.exit(1)
        return repo
    except FileNotFoundError:
        print(
            "error: 'gh' CLI not found. Install it: https://cli.github.com",
            file=sys.stderr,
        )
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        print(
            f"error: could not detect repo. Are you inside a git repository?\n{exc.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(1)


def _config_path() -> Path | None:
    """Resolve the config file path, or None if no file exists."""
    # 1. Env var override
    env = os.environ.get("GH_TODAY_CONFIG")
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None

    # 2. XDG or default ~/.config
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        base = Path(config_home)
    else:
        base = Path.home() / ".config"

    p = base / "gh-today" / "config.yml"
    return p if p.is_file() else None


def _load_raw_config() -> dict:
    """Load and parse the YAML config file. Returns {} if no file found."""
    path = _config_path()
    if path is None:
        return {}

    with open(path) as f:
        data = yaml.safe_load(f)

    return data if isinstance(data, dict) else {}


def load_config(repo: str) -> RepoConfig:
    """Load configuration for a specific repo.

    Merges values in this order (most specific wins):
      1. repos.<repo>.* (per-repo overrides)
      2. defaults.* section
      3. top-level jira_url
      4. hardcoded fallbacks
    """
    raw = _load_raw_config()

    defaults_section = raw.get("defaults") or {}
    repos_section = raw.get("repos") or {}
    repo_section = repos_section.get(repo) or {}

    # Resolve each setting: repo-specific > defaults > hardcoded
    branch = (
        repo_section.get("branch")
        or defaults_section.get("branch")
        or _HARDCODED_DEFAULTS["branch"]
    )

    action_names = repo_section.get("action_names")
    if action_names is None:
        action_names = defaults_section.get("action_names")
    if action_names is None:
        action_names = _HARDCODED_DEFAULTS["action_names"]

    # jira_url: repo-specific > defaults > top-level > hardcoded
    jira_url = (
        repo_section.get("jira_url")
        or defaults_section.get("jira_url")
        or raw.get("jira_url")
        or _HARDCODED_DEFAULTS["jira_url"]
    )

    jira_projects = repo_section.get("jira_projects")
    if jira_projects is None:
        jira_projects = defaults_section.get("jira_projects")
    if jira_projects is None:
        jira_projects = _HARDCODED_DEFAULTS["jira_projects"]

    return RepoConfig(
        repo=repo,
        branch=branch,
        action_names=list(action_names),
        jira_url=jira_url,
        jira_projects=[p.upper() for p in jira_projects],
    )

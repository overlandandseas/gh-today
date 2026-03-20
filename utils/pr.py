import re

_JIRA_TICKET_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+", re.IGNORECASE)


def extract_pr_number(message: str) -> int | None:
    """Extract a PR number from a git commit message.

    Supports two common GitHub patterns:
        - Squash merge: "Some feature (#123)"
        - Merge commit: "Merge pull request #456 from user/branch"

    Returns the PR number as an int, or None if no match is found.
    """
    match = re.search(r"(?:^Merge pull request #|\(#)(\d+)", message)
    if match:
        return int(match.group(1))
    return None


def extract_jira_ticket(*sources: str) -> str | None:
    """Search multiple text sources for a JIRA ticket ID.

    Scans each source in order and returns the first JIRA-style ticket ID
    found (e.g. PROJ-123, ENG-4567). Returns None if no match is found.

    Intended to be called with (branch_name, commit_message, pr_body) so
    that the most specific source is checked first.
    """
    for source in sources:
        if not source:
            continue
        match = _JIRA_TICKET_RE.search(source)
        if match:
            return match.group(0).upper()
    return None

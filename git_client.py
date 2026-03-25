from utils.pr import extract_jira_ticket
from utils import extract_pr_number
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum

GRAPHQL_PR_BATCH_SIZE = 50


class JobStatus(Enum):
    NOT_YET_STARTED = "dim"
    SKIPPED = "dim"
    IN_PROGRESS = "white"
    COMPLETED = "green"
    CANCELLED = "yellow"
    FAILURE = "red"

    @property
    def priority(self) -> int:
        """Higher means more relevant when choosing between runs."""
        return _STATUS_PRIORITY[self]


_STATUS_PRIORITY: dict["JobStatus", int] = {
    JobStatus.NOT_YET_STARTED: 0,
    JobStatus.SKIPPED: 0,
    JobStatus.COMPLETED: 1,
    JobStatus.CANCELLED: 2,
    JobStatus.FAILURE: 3,
    JobStatus.IN_PROGRESS: 4,
}


@dataclass
class WorkflowStatus:
    status: JobStatus
    updated_at: datetime | None
    run_number: int | None = None
    html_url: str | None = None


@dataclass
class CommitInfo:
    id: str
    author_name: str
    message: str
    author_login: str | None = None
    committed_at: datetime | None = None
    workflow_statuses: dict[str, WorkflowStatus] = field(default_factory=dict)


class GitClient:
    def __init__(
        self,
        target_date: date,
        repo: str,
        branch: str = "main",
        jira_projects: list[str] | None = None,
    ) -> None:
        self.repo = repo
        self.owner, self.name = repo.split("/")
        self.branch = branch
        self.jira_projects = jira_projects or []
        self.since = datetime.combine(
            target_date, datetime.min.time(), tzinfo=timezone.utc
        ).isoformat()
        self.until = datetime.combine(
            target_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
        ).isoformat()
        # Date-only strings for REST API query params (avoids +00:00 encoding issues)
        self.since_date = target_date.isoformat()
        self.until_date = (target_date + timedelta(days=1)).isoformat()

    def get_commits(self) -> list[CommitInfo]:
        """Fetch commits on main for the target day using GraphQL."""
        commits: list[CommitInfo] = []
        cursor = None

        while True:
            after_clause = f', after: "{cursor}"' if cursor else ""

            query = f"""
            {{
              repository(owner: "{self.owner}", name: "{self.name}") {{
                ref(qualifiedName: "refs/heads/{self.branch}") {{
                  target {{
                    ... on Commit {{
                      history(
                        first: 100
                        since: "{self.since}"
                        until: "{self.until}"
                        {after_clause}
                      ) {{
                        nodes {{
                          oid
                          message
                          committedDate
                          author {{ name user {{ login }} }}
                        }}
                        pageInfo {{ hasNextPage endCursor }}
                      }}
                    }}
                  }}
                }}
              }}
            }}
            """

            result = subprocess.run(
                ["gh", "api", "graphql", "-f", f"query={query}"],
                capture_output=True,
                text=True,
                check=True,
            )

            data = json.loads(result.stdout)
            history = data["data"]["repository"]["ref"]["target"]["history"]

            for node in history["nodes"]:
                author = node["author"]
                user = author.get("user") or {}
                committed_date_str = node.get("committedDate")
                committed_at = (
                    datetime.fromisoformat(committed_date_str)
                    if committed_date_str
                    else None
                )
                commits.append(
                    CommitInfo(
                        id=node["oid"],
                        author_name=author["name"],
                        author_login=user.get("login"),
                        message=node["message"],
                        committed_at=committed_at,
                    )
                )

            if history["pageInfo"]["hasNextPage"]:
                cursor = history["pageInfo"]["endCursor"]
            else:
                break

        return commits

    def get_workflow_names(self, workflow_files: list[str]) -> dict[str, str]:
        """Fetch display names for workflow files via the REST API.

        Returns a dict mapping workflow filename -> display name.
        E.g. {"ci.yml": "CI", "deploy.yml": "Deploy"}.
        """
        names: dict[str, str] = {}
        for wf_file in workflow_files:
            try:
                result = subprocess.run(
                    [
                        "gh",
                        "api",
                        f"/repos/{self.owner}/{self.name}/actions/workflows/{wf_file}",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                data = json.loads(result.stdout)
                names[wf_file] = data.get("name", wf_file)
            except subprocess.CalledProcessError:
                # Fallback to filename stem if the API call fails
                names[wf_file] = wf_file.removesuffix(".yml").removesuffix(".yaml")
        return names

    def get_workflow_statuses_for_workflow(
        self,
        commit_shas: set[str],
        wf_file: str,
    ) -> dict[str, WorkflowStatus]:
        """Fetch workflow run statuses for a single workflow file via REST API.

        Returns a dict mapping commit SHA -> WorkflowStatus for commits that
        have a matching run.  Commits without a run are omitted from the result.
        """
        runs_by_sha: dict[str, WorkflowStatus] = {}
        page = 1

        while True:
            try:
                result = subprocess.run(
                    [
                        "gh",
                        "api",
                        f"/repos/{self.owner}/{self.name}/actions/workflows/{wf_file}/runs"
                        f"?branch={self.branch}&created={self.since_date}..{self.until_date}"
                        f"&per_page=100&page={page}",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
            except subprocess.CalledProcessError:
                break

            data = json.loads(result.stdout)
            workflow_runs = data.get("workflow_runs", [])

            for run in workflow_runs:
                head_sha = run.get("head_sha", "")
                commit_sha = head_sha

                # For workflow_run-triggered runs, head_sha is the branch HEAD
                # at trigger time, not the commit that was actually deployed.
                # The deploy run-name embeds the real commit SHA (7+ hex chars).
                if run.get("event") == "workflow_run":
                    match = re.search(r"[0-9a-f]{7,40}", run.get("display_title", ""))
                    if match:
                        prefix = match.group()
                        for sha in commit_shas:
                            if sha.startswith(prefix):
                                commit_sha = sha
                                break

                if commit_sha not in commit_shas:
                    continue

                status_str = run.get("status", "")
                conclusion = run.get("conclusion")
                updated_at_str = run.get("updated_at")
                run_number = run.get("run_number")
                html_url = run.get("html_url")

                updated_at = (
                    datetime.fromisoformat(updated_at_str) if updated_at_str else None
                )
                job_status = self._map_job_status(status_str, conclusion)

                # Keep the most relevant run per commit (prefer active > failed > done > queued)
                existing = runs_by_sha.get(commit_sha)
                if (
                    existing is None
                    or (job_status.priority > existing.status.priority)
                    or (
                        job_status.priority == existing.status.priority
                        and updated_at
                        and existing.updated_at
                        and updated_at > existing.updated_at
                    )
                ):
                    runs_by_sha[commit_sha] = WorkflowStatus(
                        status=job_status,
                        updated_at=updated_at,
                        run_number=run_number,
                        html_url=html_url,
                    )

            total_count = data.get("total_count", 0)
            if page * 100 >= total_count:
                break
            page += 1

        return runs_by_sha

    @staticmethod
    def _map_job_status(status: str, conclusion: str | None) -> JobStatus:
        """Map GitHub REST API workflow run status/conclusion to JobStatus."""
        if status in ("queued", "waiting", "pending", "requested"):
            return JobStatus.NOT_YET_STARTED
        if status == "in_progress":
            return JobStatus.IN_PROGRESS
        if status == "completed":
            if conclusion == "skipped":
                return JobStatus.SKIPPED
            if conclusion == "cancelled":
                return JobStatus.CANCELLED
            if conclusion in (
                "failure",
                "timed_out",
                "action_required",
                "stale",
            ):
                return JobStatus.FAILURE
            return JobStatus.COMPLETED
        return JobStatus.NOT_YET_STARTED

    def get_jira_tickets(self, commits: list[CommitInfo]) -> dict[int, str | None]:
        """Batch-fetch PR details and extract JIRA tickets for all commits.

        Extracts PR numbers from commit messages, fetches all PR details
        in a single GraphQL query (batched in groups of 50), then extracts
        JIRA ticket IDs from branch names, commit messages, and PR bodies.

        Returns a dict mapping PR number -> JIRA ticket ID (or None).
        """
        # Build mapping: pr_number -> commit_message
        pr_commits: dict[int, str] = {}
        for commit in commits:
            pr_number = extract_pr_number(commit.message)
            if pr_number is not None:
                pr_commits[pr_number] = commit.message

        if not pr_commits:
            return {}

        # Fetch PR details in batches via GraphQL
        pr_numbers = list(pr_commits.keys())
        pr_data: dict[int, dict] = {}

        for i in range(0, len(pr_numbers), GRAPHQL_PR_BATCH_SIZE):
            batch = pr_numbers[i : i + GRAPHQL_PR_BATCH_SIZE]
            pr_data.update(self._fetch_pr_batch(batch))

        # Extract JIRA tickets
        results: dict[int, str | None] = {}
        for pr_number, commit_message in pr_commits.items():
            pr_info = pr_data.get(pr_number)
            if pr_info is None:
                continue

            branch_name = pr_info.get("headRefName", "")
            body = pr_info.get("body", "") or ""
            results[pr_number] = extract_jira_ticket(
                branch_name, commit_message, body, projects=self.jira_projects
            )

        return results

    def _fetch_pr_batch(self, pr_numbers: list[int]) -> dict[int, dict]:
        """Fetch a batch of PR details in a single GraphQL call.

        Returns a dict mapping PR number -> {headRefName, body},
        with missing/errored PRs omitted.
        """
        alias_fragments = "\n".join(
            f"    pr{n}: pullRequest(number: {n}) {{ headRefName body }}"
            for n in pr_numbers
        )
        query = f"""
        {{
          repository(owner: "{self.owner}", name: "{self.name}") {{
        {alias_fragments}
          }}
        }}
        """

        try:
            result = subprocess.run(
                ["gh", "api", "graphql", "-f", f"query={query}"],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError:
            return {}

        data = json.loads(result.stdout)
        repo_data = data.get("data", {}).get("repository", {})

        results: dict[int, dict] = {}
        for n in pr_numbers:
            pr_info = repo_data.get(f"pr{n}")
            if pr_info is not None:
                results[n] = pr_info

        return results

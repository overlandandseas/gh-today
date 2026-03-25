from textual.widgets.data_table import RowKey, ColumnKey
from utils import extract_pr_number
from datetime import date, datetime, timezone
from git_client import CommitInfo, GitClient, JobStatus, WorkflowStatus
from rich.text import Text
from textual import work
from textual.timer import Timer
from textual.widgets import Header, Footer, DataTable, LoadingIndicator
from textual.app import App, ComposeResult
from textual.worker import get_current_worker
import humanize
import subprocess
import webbrowser

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class TodayApp(App):
    theme = "tokyo-night"
    TITLE = "Today"

    BINDINGS = [("q", "quit", "Quit")]

    def __init__(
        self,
        target_date: date | None = None,
        action_names: list[str] | None = None,
        jira_url: str | None = None,
        repo: str = "",
        branch: str = "main",
    ) -> None:
        super().__init__()
        self.target_date = target_date or date.today()
        self.action_names = action_names or []
        self.jira_url = (jira_url or "").rstrip("/")
        self.repo = repo
        self.branch = branch
        self._row_keys: list[RowKey] = []
        self._col_keys: dict[str, ColumnKey] = {}
        self._workflow_col_keys: dict[str, ColumnKey] = {}
        self._loading_wf_files: set[str] = set()
        self._spinner_index: int = 0
        self._spinner_timer: Timer | None = None
        self._commits: list[CommitInfo] = []
        self._jira_tickets: dict[int, str | None] = {}
        self._workflow_statuses: dict[str, dict[str, WorkflowStatus]] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield LoadingIndicator(id="loading")
        yield DataTable(id="table")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.zebra_stripes = True
        table.display = False

        self._load_data()

    @work(exclusive=True, thread=True)
    def _load_data(self) -> None:
        """Fetch all data in a background thread."""
        worker = get_current_worker()

        g = GitClient(target_date=self.target_date, repo=self.repo, branch=self.branch)

        # Phase 1: fetch commits + workflow names + JIRA tickets, then show table
        commits = g.get_commits()

        if worker.is_cancelled:
            return

        workflow_names: dict[str, str] = {}
        if self.action_names:
            try:
                workflow_names = g.get_workflow_names(self.action_names)
            except Exception:
                workflow_names = {f: f.removesuffix(".yml") for f in self.action_names}

        if worker.is_cancelled:
            return

        jira_tickets = g.get_jira_tickets(commits)

        if worker.is_cancelled:
            return

        self._commits = commits
        self._jira_tickets = jira_tickets

        rows = []
        for commit in commits:
            author_cell = (
                Text(commit.author_name, style="dim")
                if commit.author_login is None
                else commit.author_name
            )
            merged_cell = Text("—", style="dim")
            if commit.committed_at:
                merged_cell = humanize.naturaltime(
                    datetime.now(timezone.utc) - commit.committed_at
                )

            row: list[str | int | None | Text] = [
                commit.id,
                author_cell,
                extract_pr_number(commit.message),
                jira_tickets.get(extract_pr_number(commit.message) or -1),
                merged_cell,
            ]
            # Spinner placeholder for each workflow column
            for _wf_file in self.action_names:
                row.append(Text(_SPINNER_FRAMES[0], style="dim"))

            rows.append(tuple(row))

        self.call_from_thread(self._populate_table, rows, workflow_names)

        # Phase 2: lazy-load each workflow column
        if not self.action_names:
            return

        commit_shas = {c.id for c in commits}

        for wf_file in self.action_names:
            if worker.is_cancelled:
                return

            try:
                statuses = g.get_workflow_statuses_for_workflow(commit_shas, wf_file)
            except Exception:
                continue

            self._workflow_statuses[wf_file] = statuses
            self.call_from_thread(
                self._update_workflow_column, wf_file, commits, statuses
            )

    @staticmethod
    def _render_job_status(wf: WorkflowStatus | None) -> Text:
        """Render a workflow status as a colored Rich Text object."""
        if wf is None:
            return Text("—", style="dim")

        style = wf.status.value
        run_str = f"#{wf.run_number} " if wf.run_number else ""

        if wf.status == JobStatus.NOT_YET_STARTED:
            return Text(f"{run_str}pending", style=style)

        if wf.status == JobStatus.SKIPPED:
            return Text(f"{run_str}skipped", style=style)

        time_ago = ""
        if wf.updated_at:
            time_ago = humanize.naturaltime(datetime.now(timezone.utc) - wf.updated_at)

        return Text(f"{run_str}{time_ago}", style=style)

    def _populate_table(
        self, rows: list[tuple], workflow_names: dict[str, str]
    ) -> None:
        """Add columns, rows, and show the table on the main thread."""
        loading = self.query_one("#loading", LoadingIndicator)
        table = self.query_one("#table", DataTable)

        self._col_keys["sha"] = table.add_column("SHA", width=12)
        self._col_keys["author"] = table.add_column("Author", width=20)
        self._col_keys["pr"] = table.add_column("PR #", width=8)
        self._col_keys["jira"] = table.add_column("Jira ID", width=10)
        self._col_keys["merged"] = table.add_column("Committed", width=18)

        for wf_file in self.action_names:
            display_name = workflow_names.get(wf_file, wf_file)
            col_key = table.add_column(display_name, width=25)
            self._workflow_col_keys[wf_file] = col_key

        self._row_keys = [table.add_row(*row) for row in rows]

        loading.display = False
        table.display = True

        # Start spinner animation for workflow columns that are still loading
        self._loading_wf_files = set(self.action_names)
        if self._loading_wf_files:
            self._spinner_index = 0
            self._spinner_timer = self.set_interval(0.08, self._tick_spinners)

    def _tick_spinners(self) -> None:
        """Advance the spinner animation in all still-loading workflow columns."""
        if not self._loading_wf_files:
            return

        self._spinner_index = (self._spinner_index + 1) % len(_SPINNER_FRAMES)
        frame = Text(_SPINNER_FRAMES[self._spinner_index], style="dim")
        table = self.query_one("#table", DataTable)

        for wf_file in self._loading_wf_files:
            col_key = self._workflow_col_keys.get(wf_file)
            if col_key is None:
                continue
            for row_key in self._row_keys:
                table.update_cell(row_key, col_key, frame)

    def _update_workflow_column(
        self,
        wf_file: str,
        commits: list,
        statuses: dict[str, WorkflowStatus],
    ) -> None:
        """Update a single workflow column with fetched statuses."""
        # Stop spinner for this column
        self._loading_wf_files.discard(wf_file)
        if not self._loading_wf_files and self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None

        table = self.query_one("#table", DataTable)
        col_key = self._workflow_col_keys.get(wf_file)
        if col_key is None:
            return

        for i, commit in enumerate(commits):
            wf_status = statuses.get(commit.id)
            table.update_cell(
                self._row_keys[i], col_key, self._render_job_status(wf_status)
            )

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        """Handle Enter on a cell — action depends on which column is selected."""
        row_key = event.cell_key.row_key
        col_key = event.cell_key.column_key

        # Find the row index to look up commit data
        try:
            row_idx = self._row_keys.index(row_key)
        except ValueError:
            return
        if row_idx >= len(self._commits):
            return

        commit = self._commits[row_idx]

        # SHA — copy full SHA to clipboard
        if col_key == self._col_keys.get("sha"):
            subprocess.run(
                ["pbcopy"],
                input=commit.id,
                text=True,
                check=False,
            )
            self.notify("Copied SHA to clipboard", timeout=2)
            return

        # Author — open GitHub profile
        if col_key == self._col_keys.get("author"):
            if commit.author_login:
                webbrowser.open(f"https://github.com/{commit.author_login}")
            return

        # PR # — open pull request
        if col_key == self._col_keys.get("pr"):
            pr_num = extract_pr_number(commit.message)
            if pr_num is not None:
                webbrowser.open(f"https://github.com/{self.repo}/pull/{pr_num}")
            return

        # Jira ID — open Jira ticket
        if col_key == self._col_keys.get("jira"):
            pr_num = extract_pr_number(commit.message)
            ticket = self._jira_tickets.get(pr_num or -1)
            if ticket and self.jira_url:
                webbrowser.open(f"{self.jira_url}/browse/{ticket}")
            return

        # Workflow columns — open the workflow run
        for wf_file, wf_col_key in self._workflow_col_keys.items():
            if col_key == wf_col_key:
                statuses = self._workflow_statuses.get(wf_file, {})
                wf_status = statuses.get(commit.id)
                if wf_status and wf_status.html_url:
                    webbrowser.open(wf_status.html_url)
                return

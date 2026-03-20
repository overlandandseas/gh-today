from datetime import timedelta, date
from argparse import ArgumentParser
from App import TodayApp
from config import detect_repo, load_config


def main():
    parser = ArgumentParser(description="View today's git commits")
    parser.add_argument(
        "--yesterday",
        action="store_true",
        help="Show yesterday's commits instead of today's",
    )

    args = parser.parse_args()

    target_date = None
    if args.yesterday:
        target_date = date.today() - timedelta(days=1)

    repo = detect_repo()
    config = load_config(repo)

    app = TodayApp(
        target_date=target_date,
        action_names=config.action_names,
        jira_url=config.jira_url,
        repo=config.repo,
        branch=config.branch,
    )
    app.run()


if __name__ == "__main__":
    main()

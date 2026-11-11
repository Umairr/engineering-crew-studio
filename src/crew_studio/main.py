"""Command line entry points.

    uv run studio-build   --project trading --requirements examples/trading_account.md
    uv run studio-improve --project trading --feedback "Add a CSV export of transactions"
    uv run studio-ui
    uv run studio-plot
"""
from __future__ import annotations

import argparse
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")


def _text_arg(value: str) -> str:
    path = Path(value)
    return path.read_text() if path.is_file() else value


def build() -> None:
    parser = argparse.ArgumentParser(description="Design, build, test and review a new product from scratch.")
    parser.add_argument("--project", required=True, help="Workspace name, e.g. trading")
    parser.add_argument("--requirements", required=True, help="Requirements text or path to a .md/.txt file")
    parser.add_argument("--rounds", type=int, default=None, help="Max automatic review->fix rounds")
    args = parser.parse_args()

    from .flow import run_studio

    flow = run_studio(args.project, mode="build", requirements=_text_arg(args.requirements), rounds=args.rounds)
    _report(flow)


def improve() -> None:
    parser = argparse.ArgumentParser(description="Give feedback on an existing product and let the team improve it.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--feedback", required=True, help="Feedback text or path to a file")
    parser.add_argument("--rounds", type=int, default=None)
    args = parser.parse_args()

    from .flow import run_studio

    flow = run_studio(args.project, mode="improve", feedback=_text_arg(args.feedback), rounds=args.rounds)
    _report(flow)


def _report(flow) -> None:
    ws = flow.ws
    print("\n" + "=" * 70)
    print(flow.state.outcome)
    if flow.state.verdict:
        print(flow.state.verdict.as_markdown())
    print(f"\nWorkspace: {ws.path}")
    print(f"Try it:    cd {ws.path} && uv run app.py")
    print(f"Tokens:    {flow.state.total_tokens:,}")


def ui() -> None:
    from .ui import launch

    launch()


def plot() -> None:
    from .flow import StudioFlow

    StudioFlow().plot("studio_flow")
    print("Wrote studio_flow.html")


if __name__ == "__main__":
    build()

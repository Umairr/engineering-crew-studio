"""CrewAI tools bound to one workspace.

Builders get read + write + run tools. Reviewers get read + run only, so the
quality inspector can never "fix" things silently: it has to report them.
"""
from __future__ import annotations

from crewai.tools import BaseTool, tool

from ..workspace import Workspace
from .smoke_harness import HARNESS


def _never_cache(*_args, **_kwargs) -> bool:
    # Files change between calls, so cached tool results would be stale.
    return False


def make_tools(ws: Workspace, *, can_write: bool) -> list[BaseTool]:
    @tool("list_files")
    def list_files() -> str:
        """List the product files in the shared workspace (flat directory, no sub-folders)."""
        names = ws.list_files()
        return "\n".join(names) if names else "The workspace is empty."

    @tool("read_file")
    def read_file(filename: str) -> str:
        """Read a file from the workspace, e.g. read_file('DESIGN.md') or read_file('accounts.py')."""
        try:
            return ws.read(filename)
        except FileNotFoundError:
            return f"No such file: {filename}. Use list_files to see what exists."
        except ValueError as exc:
            return str(exc)

    @tool("write_file")
    def write_file(filename: str, content: str) -> str:
        """Create or completely overwrite a file in the workspace with `content`.
        Use plain file names only (e.g. 'accounts.py'). Always write the COMPLETE file."""
        try:
            ws.write(filename, content)
            return f"Wrote {len(content)} characters to {filename}."
        except ValueError as exc:
            return str(exc)

    @tool("replace_in_file")
    def replace_in_file(filename: str, old_text: str, new_text: str) -> str:
        """Make a small targeted edit: replace one exact occurrence of `old_text` with `new_text`.
        Cheaper than rewriting a whole file. `old_text` must match exactly and be unique."""
        try:
            text = ws.read(filename)
        except (FileNotFoundError, ValueError) as exc:
            return str(exc)
        count = text.count(old_text)
        if count != 1:
            return f"old_text found {count} times in {filename}; it must match exactly once. Read the file and retry."
        ws.write(filename, text.replace(old_text, new_text, 1))
        return f"Edited {filename}."

    @tool("run_python")
    def run_python(filename: str) -> str:
        """Run a python file from the workspace inside its uv project (gradio available) and return
        exit code, stdout and stderr. Never run app.py this way: it would block on .launch()."""
        if filename.strip() == "app.py":
            return "Do not run app.py directly (it blocks). Use smoke_test_app to check the UI."
        try:
            ws.safe_path(filename)
        except ValueError as exc:
            return str(exc)
        return ws.run_python(filename).render()

    @tool("run_unit_tests")
    def run_unit_tests() -> str:
        """Run every test_*.py file in the workspace with the stdlib unittest runner.
        Note: unittest prints results to stderr, so read stderr carefully."""
        return ws.run_python("-m", "unittest", "discover", "-v", "-p", "test_*.py").render()

    @tool("smoke_test_app")
    def smoke_test_app() -> str:
        """Check app.py: builds the Gradio UI without launching it, calls every event handler
        with default values, and verifies each handler returns exactly as many values as it has
        output components. Must print PASS before the UI is considered done."""
        if "app.py" not in ws.list_files():
            return "app.py does not exist yet."
        harness = ws.path / "_studio_smoke_test.py"
        harness.write_text(HARNESS)
        try:
            return ws.run_python(harness.name).render()
        finally:
            harness.unlink(missing_ok=True)

    tools: list[BaseTool] = [list_files, read_file, run_python, run_unit_tests, smoke_test_app]
    if can_write:
        tools[2:2] = [write_file, replace_in_file]
    for t in tools:
        t.cache_function = _never_cache
    return tools

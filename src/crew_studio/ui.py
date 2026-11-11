"""Gradio front end for the studio: write requirements, watch the team work,
give feedback, browse the code, and launch the product that was built."""
from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import gradio as gr

from . import settings
from .progress import PROGRESS
from .workspace import Workspace, list_workspaces, slugify

EXAMPLES_DIR = settings.PROJECT_ROOT / "examples"
_run_lock = threading.Lock()
_launched: dict[str, tuple[subprocess.Popen, int]] = {}

CSS = """
#hero {padding: 18px 22px; border-radius: 16px;
       background: linear-gradient(135deg, rgba(32,157,215,.16), rgba(117,57,145,.16));
       border: 1px solid var(--border-color-primary);}
#hero h1 {margin: 0 0 4px 0;}
#hero p {margin: 0; opacity: .8;}
#activity textarea {font-family: ui-monospace, Menlo, monospace; font-size: 12.5px;}
"""


# ------------------------------------------------------------------ helpers
def _examples() -> dict[str, str]:
    return {p.stem.replace("_", " ").title(): p.read_text() for p in sorted(EXAMPLES_DIR.glob("*.md"))}


def _latest_project() -> str | None:
    names = list_workspaces()
    if not names:
        return None
    return max(names, key=lambda n: (settings.WORKSPACES_DIR / n).stat().st_mtime)


def _projects_update(selected: str | None = None):
    names = list_workspaces()
    value = selected if selected in names else (names[-1] if names else None)
    return gr.update(choices=names, value=value)


def _run_in_background(target, **kwargs):
    """Run the flow in a thread and stream the activity feed while it works."""
    if not _run_lock.acquire(blocking=False):
        yield "⏳ The team is already working on something. Wait for it to finish.", ""
        return
    PROGRESS.clear()
    box: dict = {}

    def worker():
        try:
            box["flow"] = target(**kwargs)
        except Exception as exc:  # surface any crash in the UI instead of a red toast
            box["error"] = exc
            PROGRESS.log(f"💥 {type(exc).__name__}: {exc}")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            yield PROGRESS.text(), "⏳ The team is working… (this usually takes several minutes)"
            time.sleep(1.5)
        if "error" in box:
            yield PROGRESS.text(), f"### 💥 Run failed\n`{box['error']}`"
            return
        flow = box["flow"]
        verdict = flow.state.verdict.as_markdown() if flow.state.verdict else ""
        summary = (
            f"### {flow.state.outcome}\n\n{verdict}\n\n"
            f"**Workspace:** `{flow.ws.path}`  \n**Tokens used:** {flow.state.total_tokens:,}\n\n"
            "Open the **Workspace** tab to read the code or launch the app."
        )
        yield PROGRESS.text(), summary
    finally:
        _run_lock.release()


# ----------------------------------------------------------------- handlers
def start_build(project: str, requirements: str, rounds: float):
    project, requirements = (project or "").strip(), (requirements or "").strip()
    if not project or not requirements:
        yield "", "⚠️ Please enter a project name and the requirements."
        return
    from .flow import run_studio

    yield from _run_in_background(run_studio, project=project, mode="build",
                                  requirements=requirements, rounds=int(rounds))


def start_improve(project: str | None, feedback: str, rounds: float):
    feedback = (feedback or "").strip()
    if not project or not feedback:
        yield "", "⚠️ Pick a project and write some feedback first."
        return
    from .flow import run_studio

    yield from _run_in_background(run_studio, project=project, mode="improve",
                                  feedback=feedback, rounds=int(rounds))


def load_example(name: str | None):
    if not name:
        return gr.update(), gr.update()
    return _examples().get(name, ""), slugify(name)


def open_project(project: str | None):
    if not project:
        return gr.update(choices=[], value=None), "", "_No project selected._", ""
    ws = Workspace(project)
    files = ws.list_files()
    first = "app.py" if "app.py" in files else (files[0] if files else None)
    review = ws.load_json("last_review.json")
    review_md = "_No review yet._"
    if review:
        from .models import ReviewVerdict

        review_md = ReviewVerdict.model_validate(review).as_markdown()
    return (
        gr.update(choices=files, value=first),
        ws.read_optional(first) if first else "",
        review_md,
        ws.read_optional("CHANGELOG.md"),
    )


def show_file(project: str | None, filename: str | None):
    if not project or not filename:
        return ""
    return Workspace(project).read_optional(filename)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def launch_product(project: str | None):
    if not project:
        return "⚠️ Pick a project first."
    ws = Workspace(project)
    if "app.py" not in ws.list_files():
        return "⚠️ This project has no app.py yet."
    stop_product(project)
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
    env["GRADIO_SERVER_PORT"] = str(port)
    log = open(ws.meta / "app_run.log", "w")  # noqa: SIM115 - kept open for the child process
    proc = subprocess.Popen(["uv", "run", "app.py"], cwd=ws.path, env=env, stdout=log, stderr=subprocess.STDOUT)
    _launched[project] = (proc, port)
    for _ in range(120):  # first launch may need to sync the product's venv
        time.sleep(0.5)
        if proc.poll() is not None:
            tail = Path(ws.meta / "app_run.log").read_text()[-1500:]
            return f"💥 The app exited during startup:\n\n```\n{tail}\n```"
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return f"🚀 Running at [http://127.0.0.1:{port}](http://127.0.0.1:{port}) — open it, try it, then give feedback."
    return f"⏳ Still starting… try [http://127.0.0.1:{port}](http://127.0.0.1:{port}) in a few seconds."


def stop_product(project: str | None):
    entry = _launched.pop(project or "", None)
    if entry:
        entry[0].terminate()
        return "🛑 Stopped."
    return "Nothing running for this project."


# ---------------------------------------------------------------------- UI
def build_app() -> gr.Blocks:
    examples = _examples()
    default_example = next(iter(examples), None)

    with gr.Blocks(title="Engineering Crew Studio") as demo:
        gr.HTML(
            "<div id='hero'><h1>🛠️ Engineering Crew Studio</h1>"
            "<p>Describe a product. An AI engineering team (lead, architect, backend, frontend, test, QA and "
            "product reviewer) designs, builds, tests and reviews it, then keeps improving it with your feedback.</p></div>"
        )

        with gr.Tabs():
            with gr.Tab("1 · Build"):
                with gr.Row():
                    example_pick = gr.Dropdown(list(examples), value=default_example, label="Start from an example")
                    project_name = gr.Textbox(label="Project name", value=slugify(default_example or "my-product"))
                    build_rounds = gr.Slider(1, 4, value=settings.MAX_REVIEW_ROUNDS, step=1,
                                             label="Max review rounds")
                requirements = gr.Textbox(label="Business requirements", lines=12,
                                          value=examples.get(default_example, ""),
                                          placeholder="Describe what the product should do…")
                build_btn = gr.Button("🚀 Build product", variant="primary")
                gr.Markdown("⚠️ Building replaces any existing project with the same name.")

            with gr.Tab("2 · Iterate with feedback"):
                with gr.Row():
                    improve_project = gr.Dropdown(list_workspaces(), value=_latest_project(), label="Project", scale=3)
                    refresh_btn = gr.Button("↻ Refresh", scale=1)
                    improve_rounds = gr.Slider(1, 4, value=1, step=1, label="Max review rounds", scale=2)
                feedback = gr.Textbox(label="Your feedback", lines=6,
                                      placeholder="e.g. Add a chart of portfolio value over time, and make "
                                      "withdraw errors more friendly.")
                improve_btn = gr.Button("🔁 Improve product", variant="primary")
                gr.Markdown("Your files are kept; the team edits them in place and records the change in CHANGELOG.md.")

            with gr.Tab("3 · Workspace") as ws_tab:
                with gr.Row():
                    ws_project = gr.Dropdown(list_workspaces(), value=_latest_project(), label="Project", scale=3)
                    ws_refresh = gr.Button("↻ Refresh", scale=1)
                    launch_btn = gr.Button("▶️ Launch app", variant="primary", scale=1)
                    stop_btn = gr.Button("⏹ Stop app", scale=1)
                launch_status = gr.Markdown()
                with gr.Row():
                    with gr.Column(scale=3):
                        file_pick = gr.Dropdown([], label="File")
                        code_view = gr.Code(label="Contents", language="python", lines=26, interactive=False)
                    with gr.Column(scale=2):
                        with gr.Tab("Latest review"):
                            review_view = gr.Markdown()
                        with gr.Tab("Changelog"):
                            changelog_view = gr.Markdown()

        with gr.Row():
            activity = gr.Textbox(label="Team activity", lines=18, max_lines=18, autoscroll=True,
                                  interactive=False, elem_id="activity", scale=3)
            result = gr.Markdown("Results will appear here.", label="Result")

        # wiring
        example_pick.change(load_example, inputs=[example_pick], outputs=[requirements, project_name])
        build_btn.click(start_build, inputs=[project_name, requirements, build_rounds], outputs=[activity, result]) \
            .then(_projects_update, inputs=[project_name], outputs=[improve_project]) \
            .then(_projects_update, inputs=[project_name], outputs=[ws_project])
        improve_btn.click(start_improve, inputs=[improve_project, feedback, improve_rounds], outputs=[activity, result])
        refresh_btn.click(_projects_update, inputs=[improve_project], outputs=[improve_project])
        ws_outputs = [file_pick, code_view, review_view, changelog_view]
        ws_refresh.click(_projects_update, inputs=[ws_project], outputs=[ws_project]) \
            .then(open_project, inputs=[ws_project], outputs=ws_outputs)
        ws_project.input(open_project, inputs=[ws_project], outputs=ws_outputs)
        ws_tab.select(open_project, inputs=[ws_project], outputs=ws_outputs)
        file_pick.change(show_file, inputs=[ws_project, file_pick], outputs=[code_view])
        launch_btn.click(launch_product, inputs=[ws_project], outputs=[launch_status])
        stop_btn.click(stop_product, inputs=[ws_project], outputs=[launch_status])

    return demo


def launch() -> None:
    build_app().queue().launch(css=CSS, theme=gr.themes.Soft(primary_hue="sky"))


if __name__ == "__main__":
    launch()

"""The crews the flow orchestrates.

* planning crew   - Architect turns requirements into a SystemPlan + DESIGN.md
* module crew     - HIERARCHICAL: Engineering Lead manages Backend + Test engineers for one module.
                    One of these runs per module, in parallel.
* product crew    - HIERARCHICAL: Engineering Lead manages Backend, Frontend and Test engineers
                    to build the UI, apply feedback and verify the release.
* review crew     - Quality Inspector + Product Reviewer produce a ReviewVerdict.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from crewai import LLM, Agent, Crew, Process, Task

from . import settings
from .models import ReviewVerdict, SystemPlan
from .tools.workspace_tools import make_tools
from .workspace import Workspace

if settings.USE_CONTEXT7:
    from . import mcp_patch  # noqa: F401  (fixes hyphenated MCP tool names in crewai 1.14.4)

CONFIG_DIR = Path(__file__).parent / "config"
AGENTS = yaml.safe_load((CONFIG_DIR / "agents.yaml").read_text())
TASKS = yaml.safe_load((CONFIG_DIR / "tasks.yaml").read_text())

TEAM_RULES = """

Team rules (apply to everything you do):
- The workspace is one flat directory: plain file names only, no folders, no packages.
- Python 3.13. Backend modules use the standard library only. gradio is the only third-party package.
- REQUIREMENTS.md and DESIGN.md in the workspace are the source of truth: read them before working.
- Never run app.py directly; it blocks. Use smoke_test_app for the UI.
- Prove your work with tool output (run_python, run_unit_tests, smoke_test_app) before you say it is done.
- When editing an existing file, read it first and prefer replace_in_file for small changes.
- Throwaway scripts must be named scratch_<something>.py; they are deleted automatically.
- Keep answers short: what you did, which files, and the evidence."""

GRADIO_GUIDE = """

Gradio 6 notes (changed from older versions):
- Pass css=..., theme=..., js=..., head=... to demo.launch(...), NOT to gr.Blocks(...).
- gr.Dataframe uses column_count / row_count (col_count is deprecated).
- Event listeners: button.click(fn, inputs=[...], outputs=[...]); return a tuple with exactly
  len(outputs) values, in the same order as outputs.
- Use gr.Group / gr.Row / gr.Column / gr.Tab for layout and gr.Markdown for headings.
- For dark mode, style with CSS variables (e.g. var(--body-text-color), var(--block-background-fill))
  instead of hard-coded light colours.
- If unsure about any API, check it with Context7 (library id for gradio) instead of guessing."""


def _llm() -> LLM:
    return LLM(model=settings.MODEL)


def _agent(key: str, *, tools=None, extra: str = "", mcps=None, max_iter: int | None = None) -> Agent:
    cfg = {k: v.strip() for k, v in AGENTS[key].items()}  # YAML ">" adds a trailing newline
    cfg["backstory"] = cfg["backstory"] + extra
    return Agent(
        role=cfg["role"],
        goal=cfg["goal"],
        backstory=cfg["backstory"],
        llm=_llm(),
        tools=tools or [],
        mcps=mcps or None,
        allow_delegation=False,
        max_iter=max_iter or settings.AGENT_MAX_ITER,
        verbose=True,
    )


def _task(key: str, agent: Agent | None = None, **kwargs) -> Task:
    cfg = TASKS[key]
    return Task(
        name=key,
        description=cfg["description"].strip(),
        expected_output=cfg["expected_output"].strip(),
        agent=agent,
        **kwargs,
    )


def _context7() -> list[str] | None:
    return [settings.CONTEXT7_URL] if settings.USE_CONTEXT7 else None


def _manager() -> Agent:
    # The manager must have no tools of its own; CrewAI gives it delegation tools.
    lead = _agent("engineering_lead", max_iter=settings.MANAGER_MAX_ITER)
    lead.allow_delegation = True
    return lead


def _builders(ws: Workspace, roles: list[str]) -> list[Agent]:
    tools = make_tools(ws, can_write=True)
    agents = []
    for role in roles:
        if role == "frontend_engineer":
            agents.append(_agent(role, tools=tools, extra=TEAM_RULES + GRADIO_GUIDE, mcps=_context7()))
        else:
            agents.append(_agent(role, tools=tools, extra=TEAM_RULES))
    return agents


# ------------------------------------------------------------------- crews
def planning_crew() -> Crew:
    architect = _agent("architect", extra=GRADIO_GUIDE, mcps=_context7(), max_iter=10)
    plan = _task("plan_system", architect, output_pydantic=SystemPlan)
    return Crew(agents=[architect], tasks=[plan], process=Process.sequential, verbose=True)


def module_crew(ws: Workspace) -> Crew:
    """Engineering Lead (manager) + backend & test engineers, for ONE module."""
    agents = _builders(ws, ["backend_engineer", "test_engineer"])
    tasks = [_task("implement_module"), _task("test_module")]
    return Crew(
        agents=agents,
        tasks=tasks,
        process=Process.hierarchical,
        manager_agent=_manager(),
        verbose=True,
    )


def product_crew(ws: Workspace, mode: str) -> Crew:
    """Engineering Lead (manager) decides who does what across the whole product.

    mode="integrate": build the UI then verify.   mode="improve": apply feedback then verify.
    """
    agents = _builders(ws, ["backend_engineer", "frontend_engineer", "test_engineer"])
    first = "build_ui" if mode == "integrate" else "apply_feedback"
    tasks = [_task(first), _task("verify_release")]
    return Crew(
        agents=agents,
        tasks=tasks,
        process=Process.hierarchical,
        manager_agent=_manager(),
        verbose=True,
    )


def review_crew(ws: Workspace) -> Crew:
    tools = make_tools(ws, can_write=False)
    inspector = _agent("quality_inspector", tools=tools, extra=TEAM_RULES)
    reviewer = _agent("product_reviewer", tools=tools, extra=TEAM_RULES, max_iter=12)
    inspect = _task("inspect_quality", inspector)
    verdict = _task("product_verdict", reviewer, context=[inspect], output_pydantic=ReviewVerdict)
    return Crew(agents=[inspector, reviewer], tasks=[inspect, verdict], process=Process.sequential, verbose=True)

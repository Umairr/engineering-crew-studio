"""StudioFlow: the higher-level orchestration on top of the crews.

build mode:
    prepare -> plan (architect) -> build modules IN PARALLEL (one hierarchical crew per module,
    in dependency waves) -> build UI (hierarchical product crew) -> review loop

improve mode (human feedback, workspace is kept):
    prepare -> apply feedback (hierarchical product crew) -> review loop

review loop:
    review (QA inspector + product reviewer) --approved--> done
                                             --needs_work--> fix issues -> review ...
                                             --out_of_rounds--> done (issues reported)
"""
from __future__ import annotations

import asyncio
from typing import Literal

from crewai.flow.flow import Flow, listen, or_, router, start
from pydantic import BaseModel

from . import crews, settings
from .models import ModuleSpec, ReviewVerdict, SystemPlan
from .progress import PROGRESS, register_listeners
from .workspace import Workspace


class StudioState(BaseModel):
    project: str = "product"
    mode: Literal["build", "improve"] = "build"
    requirements: str = ""
    feedback: str = ""
    max_review_rounds: int = settings.MAX_REVIEW_ROUNDS
    round: int = 0
    plan: SystemPlan | None = None
    verdict: ReviewVerdict | None = None
    total_tokens: int = 0
    outcome: str = ""


def dependency_waves(modules: list[ModuleSpec]) -> list[list[ModuleSpec]]:
    """Group modules so each wave only depends on earlier waves; each wave runs in parallel."""
    names = {m.name for m in modules}
    remaining = {m.name: m for m in modules}
    done: set[str] = set()
    waves: list[list[ModuleSpec]] = []
    while remaining:
        ready = [m for m in remaining.values() if set(m.depends_on) & names <= done]
        if not ready:  # cycle in the plan: just build the rest together
            ready = list(remaining.values())
        waves.append(ready)
        for m in ready:
            done.add(m.name)
            remaining.pop(m.name)
    return waves


class StudioFlow(Flow[StudioState]):
    # ----------------------------------------------------------------- helpers
    @property
    def ws(self) -> Workspace:
        return Workspace(self.state.project)

    def _inputs(self, **extra) -> dict:
        plan = self.state.plan
        base = {
            "product_name": plan.product_name if plan else self.state.project,
            "requirements": self.state.requirements,
            "feedback": self.state.feedback or "(none)",
            "round": str(self.state.round),
            "files": ", ".join(self.ws.list_files()) or "(none)",
            "ui_plan": plan.ui_plan if plan else "See DESIGN.md",
        }
        base.update(extra)
        return base

    def _count(self, result) -> None:
        usage = getattr(result, "token_usage", None)
        self.state.total_tokens += int(getattr(usage, "total_tokens", 0) or 0)

    # ------------------------------------------------------------------- start
    @start()
    def prepare_workspace(self):
        register_listeners()
        ws = self.ws
        if self.state.mode == "build":
            PROGRESS.log(f"🏗️  Creating fresh workspace '{ws.name}' (uv project + gradio)…")
            ws.create(fresh=True)
            ws.write("REQUIREMENTS.md", "# Requirements\n\n" + self.state.requirements.strip() + "\n")
        else:
            if not ws.exists:
                raise ValueError(f"Workspace '{ws.name}' does not exist yet. Build it first.")
            ws.create(fresh=False)
            self.state.requirements = ws.read_optional("REQUIREMENTS.md")
            saved = ws.load_json("plan.json")
            if saved:
                self.state.plan = SystemPlan.model_validate(saved)
            PROGRESS.log(f"📂 Continuing in existing workspace '{ws.name}' with your feedback.")

    @router(prepare_workspace)
    def choose_mode(self):
        return self.state.mode

    # ------------------------------------------------------------- build path
    @listen("build")
    def plan_system(self):
        PROGRESS.log("📐 Architect is designing the system…")
        result = crews.planning_crew().kickoff(inputs=self._inputs())
        self._count(result)
        plan: SystemPlan = result.pydantic
        self.state.plan = plan
        ws = self.ws
        ws.write("DESIGN.md", plan.design_markdown)
        ws.save_json("plan.json", plan.model_dump())
        modules = ", ".join(m.filename for m in plan.modules)
        ws.append_changelog("Initial design", f"{plan.summary}\n\nModules: {modules}")
        PROGRESS.log(f"📐 Plan ready: {plan.product_name} → modules: {modules}")

    async def _build_module(self, module: ModuleSpec):
        PROGRESS.log(f"🚀 Module crew started for {module.filename}")
        brief = (
            f"Purpose: {module.purpose}\n\nPublic API:\n{module.public_api}\n\n"
            f"Depends on: {', '.join(module.depends_on) or 'nothing'}"
        )
        crew = crews.module_crew(self.ws)
        result = await crew.kickoff_async(
            inputs=self._inputs(
                module_name=module.name,
                module_file=module.filename,
                test_file=module.test_filename,
                module_brief=brief,
            )
        )
        self._count(result)
        PROGRESS.log(f"✅ Module crew finished {module.filename}")
        return result.raw

    @listen(plan_system)
    async def build_modules(self):
        waves = dependency_waves(self.state.plan.modules)
        for i, wave in enumerate(waves, 1):
            PROGRESS.log(f"⚡ Wave {i}/{len(waves)}: building {', '.join(m.filename for m in wave)} in parallel")
            await asyncio.gather(*(self._build_module(m) for m in wave))
        self.ws.append_changelog("Backend built", "Modules implemented and unit-tested by parallel module crews.")

    @listen(build_modules)
    def build_ui(self):
        PROGRESS.log("🎨 Engineering Lead is directing the UI build…")
        result = crews.product_crew(self.ws, mode="integrate").kickoff(inputs=self._inputs())
        self._count(result)
        self.ws.append_changelog("UI built", result.raw[:3000])

    # ----------------------------------------------------------- improve path
    @listen("improve")
    def apply_human_feedback(self):
        PROGRESS.log("🗣️  Engineering Lead is triaging your feedback…")
        result = crews.product_crew(self.ws, mode="improve").kickoff(
            inputs=self._inputs(feedback=self.state.feedback, round="human feedback")
        )
        self._count(result)
        self.ws.append_changelog("Human feedback applied", f"**Feedback:**\n{self.state.feedback}\n\n**Changes:**\n{result.raw[:3000]}")

    # ------------------------------------------------------------ review loop
    @listen("needs_work")
    def fix_review_issues(self):
        verdict = self.state.verdict
        PROGRESS.log(f"🔁 Fixing {len(verdict.issues)} review issue(s) (round {self.state.round})…")
        result = crews.product_crew(self.ws, mode="improve").kickoff(
            inputs=self._inputs(feedback=verdict.as_feedback(), round=f"review round {self.state.round}")
        )
        self._count(result)
        self.ws.append_changelog(f"Fixes after review round {self.state.round}", result.raw[:3000])

    @router(or_(build_ui, apply_human_feedback, fix_review_issues))
    def review(self):
        self.ws.remove_scratch_files()
        self.state.round += 1
        PROGRESS.log(f"🔎 Quality review round {self.state.round}/{self.state.max_review_rounds}…")
        result = crews.review_crew(self.ws).kickoff(inputs=self._inputs())
        self._count(result)
        verdict: ReviewVerdict = result.pydantic
        # Enforce the release rule in code (LLM reviewers tend to block on cosmetic nitpicks,
        # which costs an extra paid round): approve iff there are no blocker/major issues.
        serious = [i for i in verdict.issues if i.severity in ("blocker", "major")]
        verdict.approved = not serious
        self.state.verdict = verdict
        self.ws.save_json(f"review_round_{self.state.round}.json", verdict.model_dump())
        self.ws.save_json("last_review.json", verdict.model_dump())
        self.ws.append_changelog(f"Review round {self.state.round}", verdict.as_markdown())
        PROGRESS.log(f"🔎 Verdict: {'APPROVED' if verdict.approved else 'changes requested'} ({verdict.score}/10)")
        if verdict.approved:
            return "approved"
        if self.state.round >= self.state.max_review_rounds:
            return "out_of_rounds"
        return "needs_work"

    @listen(or_("approved", "out_of_rounds"))
    def finish(self):
        self.ws.remove_scratch_files()
        v = self.state.verdict
        if v and v.approved:
            self.state.outcome = f"✅ Approved by the reviewers after {self.state.round} review round(s)."
        else:
            self.state.outcome = (
                f"⚠️ Stopped after {self.state.round} review round(s) with open issues. "
                "Give feedback to keep improving."
            )
        PROGRESS.log(f"{self.state.outcome}  Tokens used: {self.state.total_tokens:,}")
        self.ws.save_json("last_run.json", {"outcome": self.state.outcome, "tokens": self.state.total_tokens})
        return self.state.outcome


def run_studio(project: str, *, mode: str, requirements: str = "", feedback: str = "", rounds: int | None = None) -> StudioFlow:
    flow = StudioFlow()
    flow.kickoff(
        inputs={
            "project": project,
            "mode": mode,
            "requirements": requirements,
            "feedback": feedback,
            "max_review_rounds": rounds if rounds is not None else settings.MAX_REVIEW_ROUNDS,
        }
    )
    return flow

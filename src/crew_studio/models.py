"""Structured outputs exchanged between the crews and the flow."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ModuleSpec(BaseModel):
    name: str = Field(description="snake_case python module name without .py, e.g. 'accounts'")
    purpose: str = Field(description="One or two sentences: what this module is responsible for")
    public_api: str = Field(
        description="Exact classes, method/function signatures with type hints, exceptions and "
        "return shapes other modules and app.py will rely on. Signatures only, no bodies."
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="Names of other modules in this plan that this module imports. Keep this minimal.",
    )

    @property
    def filename(self) -> str:
        return f"{self.name}.py"

    @property
    def test_filename(self) -> str:
        return f"test_{self.name}.py"


class SystemPlan(BaseModel):
    product_name: str = Field(description="Short human friendly product name")
    summary: str = Field(description="2-3 sentence summary of what will be built")
    modules: list[ModuleSpec] = Field(description="1 to 4 backend modules, each buildable by one engineer")
    ui_plan: str = Field(
        description="How app.py should look and behave: sections, inputs, outputs, which backend calls each "
        "button makes, and how errors are shown to the user"
    )
    design_markdown: str = Field(
        description="The full design document in markdown for the team (architecture, modules, data model, "
        "signatures, UI, testing strategy, edge cases)"
    )


class ReviewIssue(BaseModel):
    severity: Literal["blocker", "major", "minor"]
    file: str = Field(default="", description="File the issue is in, if any")
    problem: str = Field(description="What is wrong, with evidence (test output, line, requirement)")
    fix: str = Field(description="Concrete instruction an engineer can act on")


class ReviewVerdict(BaseModel):
    approved: bool = Field(description="True only when there are no blocker or major issues")
    score: int = Field(ge=1, le=10, description="Overall quality score 1-10")
    summary: str = Field(description="Short verdict for the human")
    issues: list[ReviewIssue] = Field(default_factory=list)

    def as_feedback(self) -> str:
        if not self.issues:
            return "No issues."
        return "\n".join(
            f"{i}. [{x.severity.upper()}] {x.file + ': ' if x.file else ''}{x.problem}\n   FIX: {x.fix}"
            for i, x in enumerate(self.issues, 1)
        )

    def as_markdown(self) -> str:
        status = "✅ Approved" if self.approved else "❌ Changes requested"
        if self.approved and self.issues:
            status += " (minor polish suggestions below)"
        return f"**{status}** — score {self.score}/10\n\n{self.summary}\n\n{self.as_feedback()}"

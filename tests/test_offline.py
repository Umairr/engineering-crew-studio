"""Offline tests: no LLM calls, no API cost.

They check the flow wiring (build path, parallel waves, review loop, improve path)
by swapping the crews for fakes, and they check the UI smoke-test harness against
a deliberately broken app.

    uv run python -m unittest discover -s tests -v
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("STUDIO_RUNNER", "local")
os.environ["STUDIO_WORKSPACES_DIR"] = tempfile.mkdtemp(prefix="studio-test-")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from crew_studio import crews, flow as flow_mod  # noqa: E402
from crew_studio.models import ModuleSpec, ReviewIssue, ReviewVerdict, SystemPlan  # noqa: E402
from crew_studio.tools.workspace_tools import make_tools  # noqa: E402
from crew_studio.workspace import Workspace  # noqa: E402


class FakeResult:
    def __init__(self, raw="ok", pydantic=None):
        self.raw = raw
        self.pydantic = pydantic
        self.token_usage = mock.Mock(total_tokens=10)


class FakeCrew:
    def __init__(self, name, calls, result):
        self.name, self.calls, self.result = name, calls, result

    def kickoff(self, inputs):
        self.calls.append((self.name, inputs))
        return self.result() if callable(self.result) else self.result

    async def kickoff_async(self, inputs):
        return self.kickoff(inputs)


PLAN = SystemPlan(
    product_name="Demo",
    summary="demo",
    modules=[
        ModuleSpec(name="prices", purpose="p", public_api="def price(s: str) -> float"),
        ModuleSpec(name="accounts", purpose="a", public_api="class Account", depends_on=["prices"]),
        ModuleSpec(name="reports", purpose="r", public_api="def report() -> str"),
    ],
    ui_plan="ui",
    design_markdown="# Design",
)


def _verdicts(*approved_flags):
    it = iter(approved_flags)

    def next_result():
        ok = next(it)
        issues = [] if ok else [ReviewIssue(severity="major", problem="x", fix="y")]
        return FakeResult(pydantic=ReviewVerdict(approved=ok, score=8 if ok else 4, summary="s", issues=issues))

    return next_result


class FlowWiringTest(unittest.TestCase):
    def _patch(self, calls, verdicts):
        no_uv = mock.patch.object(Workspace, "create", lambda self, fresh: (self.path.mkdir(parents=True, exist_ok=True),
                                                                            (self.path / "pyproject.toml").write_text(""),
                                                                            self.meta.mkdir(exist_ok=True)))
        return [
            no_uv,
            mock.patch.object(crews, "planning_crew", lambda: FakeCrew("plan", calls, FakeResult(pydantic=PLAN))),
            mock.patch.object(crews, "module_crew", lambda ws: FakeCrew("module", calls, FakeResult())),
            mock.patch.object(crews, "product_crew", lambda ws, mode: FakeCrew(f"product:{mode}", calls, FakeResult())),
            mock.patch.object(crews, "review_crew", lambda ws: FakeCrew("review", calls, verdicts)),
        ]

    def _run(self, patches, **kwargs):
        for p in patches:
            p.start()
        try:
            return flow_mod.run_studio(**kwargs)
        finally:
            for p in patches:
                p.stop()

    def test_build_with_one_fix_round(self):
        calls = []
        f = self._run(self._patch(calls, _verdicts(False, True)),
                      project="demo-build", mode="build", requirements="req", rounds=3)
        names = [c[0] for c in calls]
        self.assertEqual(names, ["plan", "module", "module", "module", "product:integrate",
                                 "review", "product:improve", "review"])
        modules_built = [c[1]["module_file"] for c in calls if c[0] == "module"]
        self.assertEqual(modules_built[-1], "accounts.py")  # depends on prices -> second wave
        self.assertTrue(f.state.verdict.approved)
        self.assertIn("Approved", f.state.outcome)
        self.assertTrue((f.ws.path / "DESIGN.md").exists())
        self.assertIn("Review round 2", (f.ws.path / "CHANGELOG.md").read_text())

    def test_stops_after_max_rounds(self):
        calls = []
        f = self._run(self._patch(calls, _verdicts(False, False, False)),
                      project="demo-rounds", mode="build", requirements="req", rounds=2)
        self.assertEqual([c[0] for c in calls].count("review"), 2)
        self.assertIn("open issues", f.state.outcome)

    def test_minor_issues_only_counts_as_approved(self):
        calls = []

        def minor_only():
            return FakeResult(pydantic=ReviewVerdict(
                approved=False, score=8, summary="s",
                issues=[ReviewIssue(severity="minor", problem="polish", fix="tweak")]))

        f = self._run(self._patch(calls, minor_only), project="demo-minor", mode="build", requirements="req")
        self.assertEqual([c[0] for c in calls].count("review"), 1)
        self.assertTrue(f.state.verdict.approved)

    def test_improve_keeps_workspace(self):
        calls = []
        self._run(self._patch(calls, _verdicts(True)), project="demo-improve", mode="build", requirements="req")
        ws = Workspace("demo-improve")
        ws.write("keep_me.py", "x = 1\n")
        calls.clear()
        f = self._run(self._patch(calls, _verdicts(True)), project="demo-improve", mode="improve",
                      feedback="add export")
        self.assertEqual([c[0] for c in calls], ["product:improve", "review"])
        self.assertEqual(calls[0][1]["feedback"], "add export")
        self.assertTrue((ws.path / "keep_me.py").exists())
        self.assertEqual(f.state.plan.product_name, "Demo")


class WavesTest(unittest.TestCase):
    def test_dependency_waves(self):
        waves = flow_mod.dependency_waves(PLAN.modules)
        self.assertEqual([[m.name for m in w] for w in waves], [["prices", "reports"], ["accounts"]])


@unittest.skipUnless(os.system("uv --version > /dev/null 2>&1") == 0, "uv not installed")
class SmokeHarnessTest(unittest.TestCase):
    """Uses a real uv workspace (local runner) to prove the harness catches the output-count bug."""

    @classmethod
    def setUpClass(cls):
        cls.ws = Workspace("harness-check")
        cls.ws.create(fresh=True)
        cls.tools = {t.name: t for t in make_tools(cls.ws, can_write=True)}

    APP = '''
import gradio as gr

def handle(x):
    return ("status", 1, 2){extra}

def build_app():
    with gr.Blocks() as demo:
        inp = gr.Textbox(value="a")
        a, b, c = gr.Textbox(), gr.Number(), gr.Number()
        gr.Button("Go").click(handle, inputs=[inp], outputs=[a, b, c])
    return demo
'''

    def test_detects_output_mismatch(self):
        self.ws.write("app.py", self.APP.replace("{extra}", " + (3,)"))
        out = self.tools["smoke_test_app"].run()
        self.assertIn("returned 4 values but is wired to 3 outputs", out)

    def test_passes_good_app(self):
        self.ws.write("app.py", self.APP.replace("{extra}", ""))
        out = self.tools["smoke_test_app"].run()
        self.assertIn("PASS", out)

    def test_detects_invalid_update_for_component(self):
        # Real bug from a generated expense splitter: Dropdown-style update sent to a Textbox.
        bad = self.APP.replace('return ("status", 1, 2){extra}', 'return ("status", 1, gr.update(choices=[], value=None))') \
                      .replace("a, b, c = gr.Textbox(), gr.Number(), gr.Number()",
                               "a, b, c = gr.Textbox(), gr.Number(), gr.Textbox()")
        self.ws.write("app.py", bad)
        out = self.tools["smoke_test_app"].run()
        self.assertIn("does not accept", out)
        self.assertIn("choices", out)

    def test_rejects_paths(self):
        out = self.tools["write_file"].run(filename="../evil.py", content="x")
        self.assertIn("Invalid filename", out)
        self.assertFalse(Path(self.ws.path.parent, "evil.py").exists())


if __name__ == "__main__":
    unittest.main()

"""Source of the UI smoke test that the studio drops into a workspace and runs.

It imports `app.py`, builds the Gradio Blocks WITHOUT launching it, then calls
every event handler wired in the UI with the components' default values and
checks that:
  * the handler does not raise, and
  * the number of returned values matches the number of output components.

The second check catches the classic bug where a handler returns N+1 values
for N outputs, which makes every output box in the browser show "Error".
"""

HARNESS = r'''
import asyncio
import importlib
import inspect
import sys
import traceback

import gradio as gr


def find_blocks(mod):
    if hasattr(mod, "build_app"):
        return mod.build_app()
    for value in vars(mod).values():
        if isinstance(value, gr.Blocks):
            return value
    raise RuntimeError("app.py must define build_app() returning gr.Blocks, or a module-level `demo` Blocks object")


def default_value(component):
    value = getattr(component, "value", None)
    if callable(value):
        try:
            value = value()
        except Exception:
            value = None
    return value


def describe(component):
    label = getattr(component, "label", None)
    return f"{type(component).__name__}({label!r})" if label else type(component).__name__


def check_value(component, value):
    """Return a problem string if Gradio would reject `value` for `component`, else None."""
    if isinstance(value, dict) and value.get("__type__") == "update":
        accepted = set(inspect.signature(type(component).__init__).parameters) - {"self"}
        bad = sorted(k for k in value if k != "__type__" and k not in accepted)
        if bad:
            return (
                f"gr.update() sets {bad}, which {type(component).__name__} does not accept "
                "(e.g. 'choices' only exists on Dropdown/Radio/CheckboxGroup). Gradio raises "
                "TypeError and every output shows 'Error'."
            )
        if "value" not in value:
            return None
        value = value["value"]
    if value is None:
        return None
    try:
        component.postprocess(value)
    except Exception as exc:
        return f"Gradio cannot display the returned value {value!r:.80}: {type(exc).__name__}: {exc}"
    return None


def materialise(result):
    if inspect.iscoroutine(result):
        result = asyncio.run(result)
    if inspect.isgenerator(result):
        last = None
        for i, item in enumerate(result):
            last = item
            if i > 50:
                break
        result = last
    return result


def main():
    try:
        mod = importlib.import_module("app")
        demo = find_blocks(mod)
    except Exception:
        print("FAIL: could not import app.py / build the Blocks:")
        traceback.print_exc()
        sys.exit(1)

    problems, checked = [], 0
    for _, block_fn in demo.fns.items():
        fn = getattr(block_fn, "fn", None)
        if fn is None:
            continue
        name = getattr(fn, "__name__", repr(fn))
        inputs = list(getattr(block_fn, "inputs", []) or [])
        outputs = list(getattr(block_fn, "outputs", []) or [])
        args = [default_value(c) for c in inputs]
        checked += 1
        try:
            result = materialise(fn(*args))
        except Exception as exc:
            problems.append(
                f"{name}{tuple(args)!r}: raised {type(exc).__name__}: {exc}. "
                "Handlers must catch errors and show them in a status component instead of raising."
            )
            continue
        n_out = len(outputs)
        if n_out == 0:
            continue
        if isinstance(result, dict) and result.get("__type__") != "update":
            pairs = [(c, v) for c, v in result.items() if c in outputs]  # {component: value} style
        elif n_out == 1:
            pairs = [(outputs[0], result)]
        elif not isinstance(result, (tuple, list)):
            problems.append(f"{name}: wired to {n_out} outputs but returned a single {type(result).__name__}")
            continue
        elif len(result) != n_out:
            labels = [describe(c) for c in outputs]
            problems.append(
                f"{name}: returned {len(result)} values but is wired to {n_out} outputs {labels}. "
                "Gradio shows 'Error' in every output when these counts differ."
            )
            continue
        else:
            pairs = list(zip(outputs, result))
        for position, (component, value) in enumerate(pairs, 1):
            problem = check_value(component, value)
            if problem:
                problems.append(f"{name}: output #{position} {describe(component)}: {problem}")

    print(f"Checked {checked} event handler(s).")
    if problems:
        print("FAIL")
        for p in problems:
            print(" -", p)
        sys.exit(1)
    print("PASS: app builds and every handler returns the right number of outputs.")


if __name__ == "__main__":
    main()
'''

"""The model must stay importable without Home Assistant.

Not a style preference — it is what lets every other unit test run against the
real code with real type hints and no framework mocking. The sibling
`light_motion_profiles` component mocks the entire `homeassistant` package in
its conftest to achieve the same end; keeping the imports out is cheaper, but
only if something notices when one creeps back in.

The usual way this breaks is innocent: someone adds
`from homeassistant.const import Platform` to the package root for one constant,
and every unit test in the repo silently starts needing Home Assistant.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Imported for their side effect on sys.modules: each one runs the package
# __init__ chain above it, which is where an HA import would sneak in.
MUST_IMPORT_CLEAN = (
    "custom_components.plant_care",
    "custom_components.plant_care.model",
    "custom_components.plant_care.model.config",
    "custom_components.plant_care.model.policy",
    "custom_components.plant_care.model.naming",
    "custom_components.plant_care.store",
    "custom_components.plant_care.const",
)


def _import_in_subprocess(module: str) -> subprocess.CompletedProcess[str]:
    """Import in a fresh interpreter.

    A subprocess rather than an in-process check because by the time this test
    runs, another test in the same session may already have imported Home
    Assistant — `sys.modules` would then show it regardless of who pulled it in.
    """
    code = (
        "import sys\n"
        f"import {module}\n"
        "ha = sorted(m for m in sys.modules if m == 'homeassistant'"
        " or m.startswith('homeassistant.'))\n"
        "print(':'.join(ha))\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        # A failed import is the thing under test, so it is inspected below
        # rather than raised here.
        check=False,
    )


def test_model_imports_without_home_assistant() -> None:
    for module in MUST_IMPORT_CLEAN:
        result = _import_in_subprocess(module)
        assert result.returncode == 0, f"importing {module} failed:\n{result.stderr}"
        pulled_in = [name for name in result.stdout.strip().split(":") if name]
        assert not pulled_in, (
            f"importing {module} pulled in Home Assistant: {pulled_in}. "
            "Move the import into a function or behind TYPE_CHECKING — the unit "
            "tests run without HA installed."
        )

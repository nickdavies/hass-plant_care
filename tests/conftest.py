"""Make `import voluptuous` mean probatio, as it does inside Home Assistant.

Home Assistant aliases voluptuous to probatio when it is imported
(`homeassistant/__init__.py`), so the model never meets the real voluptuous in
production. These tests import no Home Assistant, so they do the aliasing
themselves, before any test module imports the model.
"""

from probatio.compat import install_as_voluptuous

install_as_voluptuous()

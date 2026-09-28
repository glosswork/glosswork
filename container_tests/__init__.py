"""The network-isolated container proof (FR-Q6, FR-P1, FR-Q7).

Deliberately a package outside ``pyproject.toml``'s ``testpaths`` (``["tests"]``), so
``uv run pytest -q`` never needs Docker. Run this suite explicitly with
``uv run pytest -q container_tests``.
"""

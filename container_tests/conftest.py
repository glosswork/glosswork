from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from container_tests import docker_support as ds


@pytest.fixture(scope="session")
def image_tag() -> Iterator[str]:
    """The image under test: ``GW_IMAGE`` if set, otherwise a fresh ``docker build``.

    Session-scoped so the whole module pays the build once; with the layer cache
    warm (the usual case in this repo, since the same Dockerfile backs ``docker
    build -t glosswork .``) a cache-hit build costs a few seconds, not a full
    rebuild.
    """
    tag = os.environ.get("GW_IMAGE")
    if tag:
        yield tag
        return
    yield ds.build_image(ds.DEFAULT_IMAGE_TAG)

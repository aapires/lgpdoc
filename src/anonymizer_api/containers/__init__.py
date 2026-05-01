"""Pseudonymization containers — analysis workspaces.

A ``Container`` groups documents that belong to the same analysis so
they can share a global marker mapping (``[PESSOA_0001]`` always points
to the same normalized person *within the container*). Marker
resolution is strictly scoped per container — the same marker in two
containers may point to different real values.

Sprint 1 ships CRUD only. The package is laid out so Sprint 2 can drop
in ``normalizers.py``, ``marker_resolver.py``, ``export_service.py``
etc. without re-touching the wiring.
"""
from __future__ import annotations

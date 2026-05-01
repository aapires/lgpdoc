#!/usr/bin/env python3
"""Tiny entrypoint so uvicorn can start the API: ``uvicorn scripts.run_api:app``."""
from anonymizer_api.main import create_app

app = create_app()

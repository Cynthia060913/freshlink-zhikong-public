"""B module — service layer.

Each submodule exposes pure functions that take a SQLModel
:class:`Session` and DTO arguments, returning ORM instances or
raising domain-specific exceptions. Routes import these helpers; the
helpers themselves do not import FastAPI.
"""

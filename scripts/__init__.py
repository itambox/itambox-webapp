"""Repository tooling: the stdlib-only quality gates CI and pre-commit run.

A real package rather than a namespace one so ``unittest discover`` can reach
``scripts/tests`` -- namespace-package discovery was removed in Python 3.11, and
discovery is what keeps the gate suites from being enumerated by hand.
"""

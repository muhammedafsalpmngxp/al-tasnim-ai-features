"""DYNAMIC_DB -- schema-adaptive SQL compilation, as an independent project.

A DATABASE'S BUSINESS MEANING AND ITS PHYSICAL SHAPE ARE TWO DIFFERENT
THINGS, AND ONLY ONE OF THEM IS STABLE. Tables get renamed, columns get
retyped, a mapping table gets superseded -- and until this package existed,
every one of those meant a person rewriting SQL by hand, after whatever
depended on it had already broken. This package is the layer that absorbs
that change, for any application willing to describe what it needs as a
:class:`dynamic_db.capabilities.Capability` instead of a hand-written query.

    live schema  ->  change detection  ->  affected capabilities
                 ->  SQL authored against the CURRENT schema  (reasoning)
                 ->  deterministic validation
                 ->  execution
                 ->  semantic verification                    (reasoning)
                 ->  promotion of a verified artifact
                 ->  the calling application serves it

THIS PACKAGE KNOWS NOTHING ABOUT ANY PARTICULAR APPLICATION. It has no
concept of "Daily Report", no hardcoded capability, no hardcoded rule
document, no narration/"fast" model. Everything application-specific is
supplied by the caller through :func:`dynamic_db.capabilities.register` and
:mod:`dynamic_db.service`. Physical table and column names enter this
package at exactly three points: runtime introspection (it reads them), the
rule text a caller supplies (it may legitimately describe how something is
currently stored), and the compiled artifacts this package produces (their
whole job is to name objects). See ``dynamic_db/audit.py`` for the
mechanical check that enforces this.

Independently runnable: ``python -m dynamic_db.cli status`` (and friends)
work against a configured database with no other project present at all.
See ``DYNAMIC_DB/README.md``.
"""

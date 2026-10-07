"""The graph's nodes, one module each.

Six of the eight are DETERMINISTIC and use no model at all:

    introspect        read the live database
    detect_change     compare it to the last snapshot
    affected          decide whether this capability is even touched
    validator         the hard safety and contract gate
    executor          run the candidate, bounded, read-only
    promote           put a verified artifact into service

Two are reasoning calls, and only two:

    sql_author        resolve business concepts against the current schema
    verifier          decide whether the SQL means what the rules say

That ratio is the design. Everything that can be decided from text, a
catalogue or arithmetic is decided that way, and a model is asked only the
questions that genuinely require judgement.
"""

from dynamic_db.nodes.affected import affected_node
from dynamic_db.nodes.detect_change import detect_change_node
from dynamic_db.nodes.executor import executor_node
from dynamic_db.nodes.introspect_node import introspect_node
from dynamic_db.nodes.promote import promote_node
from dynamic_db.nodes.sql_author import sql_author_node
from dynamic_db.nodes.validator import validator_node
from dynamic_db.nodes.verifier import verifier_node

__all__ = [
    "introspect_node",
    "detect_change_node",
    "affected_node",
    "sql_author_node",
    "validator_node",
    "executor_node",
    "verifier_node",
    "promote_node",
]

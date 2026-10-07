"""The agentic layer: agents plan, call read-only tools, write and verify.

    tools.py     the only way an agent reaches data -- fixed service calls
    prompts.py   planner / writer / verifier instructions
    llm.py       one model call per agent role, logged per role
    verifier.py  the deterministic number-and-wording check
    graph.py     the LangGraph plan -> act -> write -> verify graph
    service.py   runs the graph, shapes the answer for the dashboard

SQL and Python still calculate every figure; see the "Agentic amendments" in
backend/daily_report_rules.md for exactly what agents may and may not do.
"""

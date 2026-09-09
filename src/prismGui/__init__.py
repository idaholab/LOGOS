"""PRISM GUI — a Streamlit front-end for the RCPSP scheduling engine (src/CPM).

Ports-&-adapters layering (see dev_docs/):
    domain/         pure — no Streamlit, no PRISM, no jsonschema
    ports/          Protocols the application depends on
    infrastructure/ the ONLY layer that imports CPM.* / jsonschema
    application/    orchestration over domain + ports
    app/            Streamlit entry point

Imported as ``from prismGui.domain... import ...`` (``src/`` on the path, the same
root the CPM package uses).
"""

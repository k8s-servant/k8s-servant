"""k8s-servant: a read-only AI SRE agent for Kubernetes clusters.

The package is split into small modules, each with one job:

    config.py      settings from environment variables
    llm.py         the MS Foundry chat model client
    tools/         everything the model can call (MCP, kubectl)
    knowledge.py   environment description and skills (+ the load_skill tool)
    agent.py       the system prompt and the tool-calling loop
    runtime.py     wires the tools together for one session
    cli.py         the `k8s-servant` command
"""

__version__ = "0.2.0"

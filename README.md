# k8s-servant

A **read-only** AI SRE agent for Kubernetes. It reads your cluster through
[kubernetes-mcp-server](https://github.com/containers/kubernetes-mcp-server) and a
guarded `kubectl`, investigates problems with an LLM, and reports findings. It never
changes cluster state.

> **Status:** early. The code is being imported; expect breaking changes before `v1.0`.

## License

[Apache-2.0](LICENSE)

"""Initial scan: read a cluster once and turn what is found into facts.

The scan feeds the onboard command and the in-cluster re-scan. Everything it
learns is a `Fact` with evidence, so nothing reaches a knowledge file unproven.
"""

from k8s_servant.scan.facts import Fact, ScanResult
from k8s_servant.scan.run import scan

__all__ = ["Fact", "ScanResult", "scan"]

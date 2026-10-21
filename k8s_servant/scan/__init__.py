"""Initial scan: read a cluster once and turn what is found into facts.

The scan feeds the onboard command and the in-cluster re-scan. Everything it
learns is a `Fact` with evidence, so nothing reaches a knowledge file unproven.
"""

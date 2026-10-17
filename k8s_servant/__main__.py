"""Allow `python -m k8s_servant ...`, the same as the `k8s-servant` command."""

import sys

from k8s_servant.cli import main

sys.exit(main())

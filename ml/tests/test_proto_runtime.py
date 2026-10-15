"""grpcio-tools is a build-time tool: the runtime install and the import path must not need it."""

import re
import subprocess
import sys
from importlib import metadata


def test_grpcio_tools_is_not_a_runtime_requirement():
    requires = metadata.requires("corvus-ml") or []
    names = {re.split(r"[ ;<>=!~\[]", r, maxsplit=1)[0].lower() for r in requires}
    assert not any(n.startswith("grpcio") for n in names), (
        "grpcio-tools must stay in the build-time proto group, not in the runtime dependencies"
    )
    assert {"numpy", "pyarrow", "protobuf"} <= names


def test_importing_the_bindings_does_not_import_grpc_tools():
    probe = (
        "import sys, corvus_ml.proto\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('grpc_tools', 'grpc'))\n"
        "sys.exit(1 if bad else 0)\n"
    )
    # The first import may generate the bindings (needs protoc); the probe then runs on them.
    subprocess.run([sys.executable, "-c", "import corvus_ml.proto"], check=True)
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert done.returncode == 0, "importing corvus_ml.proto pulled in grpc: " + done.stderr

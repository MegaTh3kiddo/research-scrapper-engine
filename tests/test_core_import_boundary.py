import os
import subprocess
import sys
from pathlib import Path


def test_domain_and_ports_import_without_persistence_or_http_dependencies():
    source_root = Path(__file__).resolve().parents[1] / "src"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source_root)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import research_engine.domain.models; "
                "import research_engine.domain.value_objects; "
                "import research_engine.domain.events; "
                "import research_engine.ports.providers; "
                "import research_engine.ports.repositories; "
                "import research_engine.ports.snapshots; "
                "assert not any(name == 'sqlalchemy' or name.startswith('sqlalchemy.') "
                "for name in sys.modules); "
                "assert not any(name == 'httpx' or name.startswith('httpx.') "
                "for name in sys.modules)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr

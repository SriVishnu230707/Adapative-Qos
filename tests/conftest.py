import json
from pathlib import Path
import shutil

import pytest

from adaptive_qos.config import load_runtime_config
from adaptive_qos.models import record_from_json

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def payload():
    def load(filename):
        return json.loads((ROOT / "examples/contracts/valid" / filename).read_text(encoding="utf-8"))
    return load


@pytest.fixture
def parse():
    return lambda data: record_from_json(json.dumps(data))


@pytest.fixture
def runtime():
    return load_runtime_config(ROOT / "configs")


@pytest.fixture
def records():
    return tuple(record_from_json(path.read_bytes()) for path in sorted((ROOT / "examples/contracts/valid").glob("*.json")))


@pytest.fixture
def config_dir(tmp_path):
    destination = tmp_path / "configs"
    shutil.copytree(ROOT / "configs", destination)
    return destination

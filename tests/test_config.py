from pathlib import Path
import yaml


def test_ownership_rules_exist():
    path = Path("config/ownership.yml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["realtime"]["owner"] == "ststat"
    assert data["realtime"]["executor"] == "cloudflare_worker"
    assert any("never overwrite realtime" in rule.lower() for rule in data["rules"])

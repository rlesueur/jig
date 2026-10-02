from pathlib import Path

import pytest

from jig.config import DEFAULT_CONFIG_PATH, ConfigError, load_config


def test_config_saved_with_a_byte_order_mark_loads(tmp_path: Path):
    plain = Path(DEFAULT_CONFIG_PATH).read_text(encoding="utf-8")
    bom = tmp_path / "jig.toml"
    bom.write_bytes(b"\xef\xbb\xbf" + plain.encode("utf-8"))

    from_plain = load_config(DEFAULT_CONFIG_PATH, data_dir=tmp_path / "a", sandbox_dir=tmp_path / "sa")
    from_bom = load_config(bom, data_dir=tmp_path / "b", sandbox_dir=tmp_path / "sb")
    assert from_bom.model.base_url == from_plain.model.base_url
    assert from_bom.model.name == from_plain.model.name


def test_invalid_toml_is_a_config_error_naming_the_file(tmp_path: Path):
    bad = tmp_path / "jig.toml"
    bad.write_text("[model\nname = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="jig.toml is not valid TOML"):
        load_config(bad)

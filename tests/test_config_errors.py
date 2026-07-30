"""Config loading is the most common novice activity, so its failures have to
say three things: which file, which section/key, and what to do about it.

AppConfig.load() validates all nine sections regardless of active flags, which
means a mistake in a subsystem the user never enabled still stops the run --
all the more reason the message must point at the right place.
"""

import tomllib

import pytest

from src.utils.config import AppConfig

EXAMPLE = "configs/config.example.toml"

# Sections with at least one key that has no sensible default. Missing these
# entirely is a mistake, not a request for defaults.
REQUIRED_SECTIONS = ["zmq", "camera", "liquid_lens", "opto_trigger"]


def _config_without_section(tmp_path, section: str):
    """Write a copy of the example config with one whole section removed.

    Edits the file as text rather than round-tripping through tomllib, so the
    result stays valid TOML -- otherwise these tests would be asserting on a
    syntax error rather than on the missing section.
    """
    keep, dropping = [], False
    for line in open(EXAMPLE).read().splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            header = stripped.strip("[]")
            dropping = header == section or header.startswith(f"{section}.")
        if not dropping:
            keep.append(line)

    out = tmp_path / "missing_section.toml"
    out.write_text("\n".join(keep) + "\n")

    remaining = tomllib.loads(out.read_text())
    assert section not in remaining, f"helper failed to drop [{section}]"
    return out


@pytest.mark.parametrize("section", REQUIRED_SECTIONS)
def test_missing_section_names_the_section_the_file_and_the_fix(tmp_path, section):
    path = _config_without_section(tmp_path, section)

    with pytest.raises(ValueError) as exc:
        AppConfig.load(str(path))

    message = str(exc.value)
    assert f"[{section}]" in message, f"must name the missing section: {message}"
    assert str(path) in message, f"must name the config file: {message}"
    assert "config.example.toml" in message, f"must say where to copy it from: {message}"


def test_invalid_value_names_the_config_file(tmp_path):
    """A bad value inside a present section must still identify the file --
    with several configs around (example, local, per-experiment copies) the
    message is useless without it."""
    source = open(EXAMPLE).read()
    import re
    source = re.sub(r'(sham_probability\s*=\s*)-?\d+\.?\d*', r'\g<1>5.0', source)
    path = tmp_path / "bad_value.toml"
    path.write_text(source)

    with pytest.raises(ValueError) as exc:
        AppConfig.load(str(path))

    message = str(exc.value)
    assert str(path) in message, f"must name the config file: {message}"
    assert "sham_probability" in message


def test_a_valid_config_still_loads(tmp_path):
    """Guard against the error handling rejecting good input."""
    assert AppConfig.load(EXAMPLE) is not None


def test_optional_sections_may_be_absent(tmp_path):
    """[monitoring], [logging] and [visual_stimuli] are entirely defaulted --
    omitting them must keep working, or existing configs break."""
    source = open(EXAMPLE).read()
    for section in ("[monitoring]", "[logging]", "[visual_stimuli]"):
        assert section in source
    trimmed = "\n".join(
        line
        for line in source.splitlines()
        if not line.startswith(("active =", "host =", "port = 5000", "level ="))
    )
    path = tmp_path / "no_optional.toml"
    path.write_text(trimmed.replace("[monitoring]", "").replace("[logging]", ""))

    config = AppConfig.load(str(path))

    assert config.monitoring.active is False
    assert config.logging.level == "INFO"


class TestMainCliMessages:
    """main.load_config() is what a novice actually sees."""

    def test_missing_file_suggests_the_cp_command(self, caplog):
        from main import load_config

        with pytest.raises(SystemExit):
            load_config("configs/definitely_not_here.toml")

        combined = " ".join(r.message for r in caplog.records)
        assert "configs/definitely_not_here.toml" in combined
        assert "cp configs/config.example.toml" in combined, (
            f"a missing config is almost always a skipped cp step: {combined}"
        )

    def test_toml_syntax_error_is_reported_as_such(self, tmp_path, caplog):
        path = tmp_path / "broken.toml"
        path.write_text("[zmq]\nbraid_port = = 5555\n")

        from main import load_config

        with pytest.raises(SystemExit):
            load_config(str(path))

        combined = " ".join(r.message for r in caplog.records)
        assert "not valid TOML" in combined, f"must distinguish a syntax error: {combined}"
        assert str(path) in combined

    def test_invalid_configuration_is_distinguished_from_a_syntax_error(
        self, tmp_path, caplog
    ):
        source = open(EXAMPLE).read().replace('color = "red"', 'color = "chartreuse"')
        path = tmp_path / "bad_colour.toml"
        path.write_text(source)

        from main import load_config

        with pytest.raises(SystemExit):
            load_config(str(path))

        combined = " ".join(r.message for r in caplog.records)
        assert "invalid configuration" in combined.lower()
        assert "chartreuse" in combined
        assert "not valid TOML" not in combined

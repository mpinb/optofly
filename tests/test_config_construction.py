"""Structural guarantees about how config objects are built.

Every config class used to be constructed with

    instance = object.__new__(cls)
    object.__setattr__(instance, "__dict__", dict(...))

because a path-based __init__ shadowed the dataclass-generated one. That made
the declared field list and the construction dict two independent lists with
nothing keeping them in sync: add a field to the dataclass, forget the dict,
and there is no error at config-load time -- you get an AttributeError later,
inside a child process, at trigger time. These tests pin the invariant that
the declared fields *are* the construction interface.
"""

import dataclasses

import pytest

from src.utils.config import (
    AppConfig,
    BraidPublisherConfig,
    CameraConfig,
    LiquidLensConfig,
    LoggingConfig,
    OptoTriggerConfig,
    TriggerHandlerConfig,
    VisualStimuliConfig,
    ZMQConfig,
)

CONFIG = "configs/config.example.toml"

SECTION_CLASSES = [
    ZMQConfig,
    CameraConfig,
    TriggerHandlerConfig,
    LiquidLensConfig,
    BraidPublisherConfig,
    OptoTriggerConfig,
    LoggingConfig,
    VisualStimuliConfig,
]

ATTR_FOR_CLASS = {
    ZMQConfig: "zmq",
    CameraConfig: "camera",
    TriggerHandlerConfig: "trigger_handler",
    LiquidLensConfig: "liquid_lens",
    BraidPublisherConfig: "braid_publisher",
    OptoTriggerConfig: "opto_trigger",
    LoggingConfig: "logging",
    VisualStimuliConfig: "visual_stimuli",
}


@pytest.fixture(scope="module")
def app_config():
    return AppConfig.load(CONFIG)


@pytest.mark.parametrize("cls", SECTION_CLASSES, ids=lambda c: c.__name__)
def test_config_is_constructible_from_its_declared_fields(cls, app_config):
    """The dataclass __init__ must be the real construction path.

    If it isn't, a field can be declared but never assigned, and the omission
    is invisible until something reads it at runtime.
    """
    reference = getattr(app_config, ATTR_FOR_CLASS[cls])
    values = {f.name: getattr(reference, f.name) for f in dataclasses.fields(cls)}

    clone = cls(**values)

    assert clone == reference


@pytest.mark.parametrize("cls", SECTION_CLASSES, ids=lambda c: c.__name__)
def test_every_declared_field_is_populated_by_load(cls, app_config):
    """Drift in the other direction: a declared field that load() never sets."""
    instance = getattr(app_config, ATTR_FOR_CLASS[cls])

    missing = [f.name for f in dataclasses.fields(cls) if not hasattr(instance, f.name)]

    assert missing == [], f"{cls.__name__} declares fields load() never sets: {missing}"


@pytest.mark.parametrize("cls", SECTION_CLASSES, ids=lambda c: c.__name__)
def test_instances_carry_no_undeclared_attributes(cls, app_config):
    """The reverse drift: something assigned that isn't a declared field, which
    type checkers and readers of the dataclass would never know about."""
    instance = getattr(app_config, ATTR_FOR_CLASS[cls])
    declared = {f.name for f in dataclasses.fields(cls)}

    extra = set(vars(instance)) - declared

    assert extra == set(), f"{cls.__name__} carries undeclared attributes: {extra}"


@pytest.mark.parametrize(
    "cls",
    [c for c in SECTION_CLASSES if c is not OptoTriggerConfig],
    ids=lambda c: c.__name__,
)
def test_frozen_configs_actually_reject_mutation(cls, app_config):
    """OptoTriggerConfig is deliberately mutable (set_parameters writes the
    per-trial randomised values). Every other config must be immutable."""
    instance = getattr(app_config, ATTR_FOR_CLASS[cls])
    field_name = dataclasses.fields(cls)[0].name

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field_name, "mutated")


@pytest.mark.parametrize("cls", SECTION_CLASSES, ids=lambda c: c.__name__)
def test_from_path_returns_the_same_section_as_app_config(cls, app_config):
    """The path-based convenience form must stay in agreement with AppConfig
    rather than being a second, drifting way to build the same object."""
    assert cls.from_path(CONFIG) == getattr(app_config, ATTR_FOR_CLASS[cls])


def test_app_config_is_constructible_from_its_declared_fields(app_config):
    values = {
        f.name: getattr(app_config, f.name) for f in dataclasses.fields(AppConfig)
    }

    assert AppConfig(**values) == app_config

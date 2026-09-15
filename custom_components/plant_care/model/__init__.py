"""The plant care domain model — pure Python, no Home Assistant.

Everything in this package can be imported and tested without Home Assistant
installed, which is why the unit tests need no framework mocking. The Home
Assistant edge lives in the platform modules one level up.
"""

from .config import InvalidPlantConfig, parse, parse_calibration, schema
from .plant import (
    Calibrated,
    Calibrating,
    Calibration,
    CareTask,
    Domain,
    Entity,
    Moisture,
    Plant,
    ProbeFacts,
    SourceEntity,
)
from .policy import DEFAULT_POLICY, Policy

__all__ = [
    "DEFAULT_POLICY",
    "Calibrated",
    "Calibrating",
    "Calibration",
    "CareTask",
    "Domain",
    "Entity",
    "InvalidPlantConfig",
    "Moisture",
    "Plant",
    "Policy",
    "ProbeFacts",
    "SourceEntity",
    "parse",
    "parse_calibration",
    "schema",
]

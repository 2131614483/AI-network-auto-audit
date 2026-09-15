"""CW1 port contracts: the stable port vocabulary for the execution IR.

Every port carries the full contract surface required by 方案 5.1:
``port_id``, ``direction``, ``schema_ref``, ``schema_version``,
``schema_sha256``, ``media_type``, ``required``, ``cardinality``,
``classification`` and ``transport``.  A port id is stable within a node;
``(node_instance_id, port_id)`` is the only legal handle for a data edge.
"""

from __future__ import annotations

from dataclasses import dataclass

_DIRECTIONS = ("input", "output")
_CARDINALITIES = ("one", "many")
_TRANSPORTS = ("artifact_ref",)

SUPPORTED_PORT_FIELDS = frozenset(
    {
        "port_id",
        "direction",
        "schema_ref",
        "schema_version",
        "schema_sha256",
        "media_type",
        "required",
        "cardinality",
        "classification",
        "transport",
    }
)


class PortContractError(ValueError):
    """A port definition violates the CW1 port schema."""


@dataclass(frozen=True, slots=True)
class PortContract:
    """One typed port on a plan node (deterministic, hashable)."""

    port_id: str
    direction: str
    schema_ref: str
    schema_version: str
    schema_sha256: str
    media_type: str
    required: bool = True
    cardinality: str = "one"
    classification: str = "internal"
    transport: str = "artifact_ref"

    def as_dict(self) -> dict[str, object]:
        return {
            "port_id": self.port_id,
            "direction": self.direction,
            "schema_ref": self.schema_ref,
            "schema_version": self.schema_version,
            "schema_sha256": self.schema_sha256,
            "media_type": self.media_type,
            "required": self.required,
            "cardinality": self.cardinality,
            "classification": self.classification,
            "transport": self.transport,
        }


def validate_port(port: dict[str, object], *, node_instance_id: str) -> PortContract:
    """Validate one raw port dict against the CW1 port schema.

    Raises :class:`PortContractError` on an unknown field, a duplicated port
    id (checked by the caller across one node), a bad direction/cardinality/
    transport, or a missing required field.  Fail-closed: never guess.
    """
    unknown = set(port) - SUPPORTED_PORT_FIELDS
    if unknown:
        raise PortContractError(
            f"unsupported field(s) on port of node {node_instance_id}: {sorted(unknown)}"
        )
    port_id = port.get("port_id")
    if not isinstance(port_id, str) or not port_id.strip():
        raise PortContractError(f"port of node {node_instance_id} must declare port_id")
    direction = port.get("direction")
    if direction not in _DIRECTIONS:
        raise PortContractError(f"port {port_id!r} of node {node_instance_id} has bad direction")
    cardinality = port.get("cardinality", "one")
    if cardinality not in _CARDINALITIES:
        raise PortContractError(f"port {port_id!r} of node {node_instance_id} has bad cardinality")
    transport = port.get("transport", "artifact_ref")
    if transport not in _TRANSPORTS:
        raise PortContractError(f"port {port_id!r} of node {node_instance_id} has bad transport")
    for field in ("schema_ref", "schema_version", "schema_sha256", "media_type"):
        value = port.get(field)
        if not isinstance(value, str) or not value:
            raise PortContractError(
                f"port {port_id!r} of node {node_instance_id} must declare {field}"
            )
    return PortContract(
        port_id=port_id,
        direction=direction,
        schema_ref=str(port["schema_ref"]),
        schema_version=str(port["schema_version"]),
        schema_sha256=str(port["schema_sha256"]),
        media_type=str(port["media_type"]),
        required=bool(port.get("required", True)),
        cardinality=cardinality,
        classification=str(port.get("classification") or "internal"),
        transport=transport,
    )

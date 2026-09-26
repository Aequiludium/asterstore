"""Separate management, byte stability and historical discovery."""

from dataclasses import dataclass
from enum import StrEnum

from asterstore.errors import InvalidDeclarationError, UnsupportedCapabilityError


class Management(StrEnum):
    MANAGED = "managed"
    REGISTERED = "registered"


class ByteStability(StrEnum):
    COORDINATED_IMMUTABLE = "coordinated_immutable"
    PRODUCER_IMMUTABLE = "producer_immutable"
    MUTABLE_OR_UNKNOWN = "mutable_or_unknown"


class HistoryAccess(StrEnum):
    CURRENT_ONLY = "current_only"
    VERSIONED = "versioned"


class RetentionScope(StrEnum):
    METADATA = "metadata"
    OBJECTS = "objects"


@dataclass(frozen=True, slots=True)
class Capabilities:
    """A policy declaration, not evidence of ownership, retention or durability.

    Historical discovery concerns declarations. External producer immutability
    is a producer assertion; it never grants coordinated retention or deletion.
    """

    management: Management
    byte_stability: ByteStability
    history: HistoryAccess = HistoryAccess.CURRENT_ONLY

    def __post_init__(self) -> None:
        for value, kind in (
            (self.management, Management),
            (self.byte_stability, ByteStability),
            (self.history, HistoryAccess),
        ):
            if not isinstance(value, kind):
                raise InvalidDeclarationError(f"expected a {kind.__name__} value")
        coordinated = self.byte_stability is ByteStability.COORDINATED_IMMUTABLE
        if (self.management is Management.MANAGED) != coordinated:
            raise InvalidDeclarationError(
                "managed requires coordinated immutability; registered cannot claim coordination"
            )

    @classmethod
    def managed(cls, *, history: HistoryAccess = HistoryAccess.CURRENT_ONLY) -> "Capabilities":
        return cls(Management.MANAGED, ByteStability.COORDINATED_IMMUTABLE, history)

    @classmethod
    def registered(
        cls,
        *,
        byte_stability: ByteStability = ByteStability.MUTABLE_OR_UNKNOWN,
        history: HistoryAccess = HistoryAccess.CURRENT_ONLY,
    ) -> "Capabilities":
        return cls(Management.REGISTERED, byte_stability, history)

    @property
    def retention_scopes(self) -> frozenset[RetentionScope]:
        """Scopes the declared profile can support when a governance service owns it."""
        if self.management is Management.MANAGED:
            return frozenset((RetentionScope.METADATA, RetentionScope.OBJECTS))
        return frozenset((RetentionScope.METADATA,))

    def require_retention(self, scope: RetentionScope) -> None:
        """Check policy eligibility only; never create or prove a retention record."""
        if not isinstance(scope, RetentionScope):
            raise InvalidDeclarationError("scope must be a RetentionScope value")
        if scope not in self.retention_scopes:
            raise UnsupportedCapabilityError("registered objects cannot receive object retention")

    def require_managed(self) -> None:
        """Necessary policy check for managed operations, never deletion authorization."""
        if self.management is not Management.MANAGED:
            raise UnsupportedCapabilityError(
                "registered data has no managed write or delete rights"
            )

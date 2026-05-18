"""
Data model for the carpool app.

These dataclasses mirror what the database tables will look like later. Every
entity has an `id` and a `group_id`, so when we move to a real database (likely
Postgres), the migration is mostly mechanical. We're already thinking
multi-tenant from day one, even though we only have one group right now.

Design notes:

- Family is the unit that has one or more kids in the carpool. A family has
  one or more addresses (one for most, two for divorced co-parents, more for
  unusual setups). The actual pickup address for a given trip is decided each
  night during confirmation — we don't model custody schedules.

- Guardian is an adult attached to a family. Has a phone and email. The
  `is_driver` flag controls whether they're in the rotation. The other parent
  in a divorced household might be a guardian (so they get notifications) but
  not a driver.

- Kid is just a kid attached to a family. The carpool transports kids, but the
  notifications go to guardians.

- Address has a label ("Mom's house", "Dad's house") and lat/long that we
  cache after geocoding once. Re-geocoding every run would waste API calls.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Group:
    """A carpool group. For v0 there's only one, but we tag everything with
    group_id so multi-group support is a small change later."""
    id: str
    name: str


@dataclass
class Address:
    id: str
    group_id: str
    family_id: str
    label: str  # e.g. "Home", "Mom's house", "Dad's house"
    street: str
    # lat/long get filled in after geocoding. None means not-yet-geocoded.
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @property
    def is_geocoded(self) -> bool:
        return self.latitude is not None and self.longitude is not None


@dataclass
class Guardian:
    id: str
    group_id: str
    family_id: str
    name: str
    phone: str
    email: str
    is_driver: bool = True  # in the driving rotation?


@dataclass
class Kid:
    id: str
    group_id: str
    family_id: str
    name: str


@dataclass
class Family:
    id: str
    group_id: str
    name: str  # e.g. "The Patel Family"
    guardians: list[Guardian] = field(default_factory=list)
    kids: list[Kid] = field(default_factory=list)
    addresses: list[Address] = field(default_factory=list)

    @property
    def has_address(self) -> bool:
        return bool(self.addresses)

    @property
    def primary_address(self) -> Address:
        """For v0, just use the first address. Once we have nightly
        confirmations, the actual address used per trip comes from there."""
        if not self.addresses:
            raise ValueError(f"Family {self.name!r} has no address on file. "
                             "Ask them to update their profile.")
        return self.addresses[0]


@dataclass
class Destination:
    """A place the carpool goes to — school, soccer field, etc.
    Coordinator-managed list, used per trip."""
    id: str
    group_id: str
    name: str
    street: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @property
    def is_geocoded(self) -> bool:
        return self.latitude is not None and self.longitude is not None

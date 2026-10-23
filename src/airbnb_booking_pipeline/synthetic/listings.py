"""The closed list of fictional listings every synthetic email draws from."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Listing:
    room_id: int
    name: str


LISTINGS: tuple[Listing, ...] = (
    Listing(900001, "Loft Aurora 101"),
    Listing(900002, "Studio Boreal 202"),
    Listing(900003, "Casa Cedro"),
    Listing(900004, "Apartamento Duna 304"),
    Listing(900005, "Studio Eclipse 505"),
    Listing(900006, "Flat Fênix 606"),
)

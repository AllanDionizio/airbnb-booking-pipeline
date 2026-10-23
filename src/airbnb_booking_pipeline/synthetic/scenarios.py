"""
The 18 regression scenarios: 13 confirmations and 5 cancellations.

Each scenario covers one layout variant seen in production. The expected
parser output is written by hand next to the spec instead of being derived
from it: the money mapping (which label lands in which field, with which
sign) is exactly what the tests must check, so it cannot come from the same
code path.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .emails import CancellationSpec, ConfirmationSpec
from .listings import LISTINGS

AURORA, BOREAL, CEDRO, DUNA, ECLIPSE, FENIX = LISTINGS
_BRT = timezone(timedelta(hours=-3))
_SERVICE_FEE = "Taxa de serviço do anfitrião (4.0% + IVA)"


def _at(year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_BRT)


@dataclass(frozen=True)
class ConfirmationScenario:
    name: str
    description: str
    spec: ConfirmationSpec
    expected_payout_lines: dict[str, float]


@dataclass(frozen=True)
class CancellationScenario:
    name: str
    description: str
    spec: CancellationSpec
    expected_outcome: str
    expected_penalty: float | None
    expected_currency: str | None


CONFIRMATIONS: tuple[ConfirmationScenario, ...] = (
    ConfirmationScenario(
        "simple",
        "Two nights, short-stay cleaning fee, service fee. The baseline layout.",
        ConfirmationSpec(
            code="HMTEST1001",
            guest="Guest Alpha",
            listing=AURORA,
            check_in=date(2026, 6, 12),
            check_out=date(2026, 6, 14),
            email_date=_at(2026, 5, 20),
            adults=2,
            other_guests=None,
            payout_lines=(
                ("Preço da acomodação para 2 noites", 480.00),
                ("Taxa de limpeza para estadias curtas", 90.00),
                (_SERVICE_FEE, -23.94),
            ),
        ),
        {"accommodation": 480.00, "cleaning_fee": 90.00, "host_service_fee": 23.94},
    ),
    ConfirmationScenario(
        "with_children",
        "Children on the guests line (only adults count) and a positive nightly price adjustment.",
        ConfirmationSpec(
            code="HMTEST1002",
            guest="Guest Bravo",
            listing=CEDRO,
            check_in=date(2026, 7, 6),
            check_out=date(2026, 7, 9),
            email_date=_at(2026, 5, 11),
            adults=3,
            other_guests="1 criança",
            payout_lines=(
                ("Preço da acomodação para 3 noites", 735.00),
                ("Taxa de limpeza", 110.00),
                ("Ajuste de preço por noite", 36.00),
                (_SERVICE_FEE, -36.95),
            ),
        ),
        {
            "accommodation": 735.00,
            "cleaning_fee": 110.00,
            "other_income": 36.00,
            "host_service_fee": 36.95,
        },
    ),
    ConfirmationScenario(
        "with_infant",
        "An infant on the guests line, short-stay cleaning fee and a positive adjustment.",
        ConfirmationSpec(
            code="HMTEST1003",
            guest="Guest Charlie",
            listing=DUNA,
            check_in=date(2026, 10, 10),
            check_out=date(2026, 10, 12),
            email_date=_at(2026, 5, 28),
            adults=2,
            other_guests="1 bebê",
            payout_lines=(
                ("Preço da acomodação para 2 noites", 398.00),
                ("Taxa de limpeza para estadias curtas", 85.00),
                ("Ajuste de preço por noite", 18.50),
                (_SERVICE_FEE, -21.04),
            ),
        ),
        {
            "accommodation": 398.00,
            "cleaning_fee": 85.00,
            "other_income": 18.50,
            "host_service_fee": 21.04,
        },
    ),
    ConfirmationScenario(
        "with_pet",
        "Pet fee, listed after the service fee as Airbnb does.",
        ConfirmationSpec(
            code="HMTEST1004",
            guest="Guest Delta",
            listing=AURORA,
            check_in=date(2026, 5, 22),
            check_out=date(2026, 5, 24),
            email_date=_at(2026, 5, 3),
            adults=2,
            other_guests="1 animal de estimação",
            payout_lines=(
                ("Preço da acomodação para 2 noites", 456.00),
                ("Taxa de limpeza", 95.00),
                (_SERVICE_FEE, -23.15),
                ("Taxa de animais de estimação", 40.00),
            ),
        ),
        {
            "accommodation": 456.00,
            "cleaning_fee": 95.00,
            "host_service_fee": 23.15,
            "pet_fee": 40.00,
        },
    ),
    ConfirmationScenario(
        "next_year_stay",
        "Stay entirely in the next year: both dates carry an explicit year.",
        ConfirmationSpec(
            code="HMTEST1005",
            guest="Guest Echo",
            listing=CEDRO,
            check_in=date(2027, 1, 6),
            check_out=date(2027, 1, 8),
            email_date=_at(2026, 5, 15),
            adults=4,
            other_guests="2 crianças",
            payout_lines=(
                ("Preço da acomodação para 2 noites", 612.00),
                ("Taxa de limpeza para estadias curtas", 120.00),
                (_SERVICE_FEE, -31.47),
            ),
        ),
        {"accommodation": 612.00, "cleaning_fee": 120.00, "host_service_fee": 31.47},
    ),
    ConfirmationScenario(
        "long_stay_discount",
        "Two-month stay with a plain 'Desconto' line and a regular space after 'R$'.",
        ConfirmationSpec(
            code="HMTEST1006",
            guest="Guest Foxtrot",
            listing=BOREAL,
            check_in=date(2026, 8, 2),
            check_out=date(2026, 10, 9),
            email_date=_at(2026, 7, 14),
            adults=2,
            other_guests=None,
            payout_lines=(
                ("Preço da acomodação para 68 noites", 9520.00),
                ("Taxa de limpeza", 145.00),
                ("Desconto", -1904.00),
                (_SERVICE_FEE, -334.72),
            ),
            money_sep=" ",
        ),
        {
            "accommodation": 9520.00,
            "cleaning_fee": 145.00,
            "discount": 1904.00,
            "host_service_fee": 334.72,
        },
    ),
    ConfirmationScenario(
        "monthly_discount",
        "Four-week stay with a 'Desconto mensal' line.",
        ConfirmationSpec(
            code="HMTEST1007",
            guest="Guest Golf",
            listing=FENIX,
            check_in=date(2026, 6, 5),
            check_out=date(2026, 7, 3),
            email_date=_at(2026, 5, 19),
            adults=2,
            other_guests=None,
            payout_lines=(
                ("Preço da acomodação para 28 noites", 4760.00),
                ("Taxa de limpeza", 130.00),
                ("Desconto mensal", -952.00),
                (_SERVICE_FEE, -169.34),
            ),
        ),
        {
            "accommodation": 4760.00,
            "cleaning_fee": 130.00,
            "discount": 952.00,
            "host_service_fee": 169.34,
        },
    ),
    ConfirmationScenario(
        "usd",
        "USD-priced listing: '$' with an nbsp and a negative adjustment (a discount).",
        ConfirmationSpec(
            code="HMTEST1008",
            guest="Guest Hotel",
            listing=ECLIPSE,
            check_in=date(2026, 7, 20),
            check_out=date(2026, 7, 23),
            email_date=_at(2026, 7, 1),
            adults=2,
            other_guests=None,
            payout_lines=(
                ("Preço da acomodação para 3 noites", 285.00),
                ("Taxa de limpeza", 30.00),
                ("Ajuste de preço por noite", -15.00),
                (_SERVICE_FEE, -10.80),
            ),
            currency="USD",
            money_sep="\xa0",
        ),
        {
            "accommodation": 285.00,
            "cleaning_fee": 30.00,
            "discount": 15.00,
            "host_service_fee": 10.80,
        },
    ),
    ConfirmationScenario(
        "long_stay",
        "Two weeks across a month boundary with a negative nightly adjustment.",
        ConfirmationSpec(
            code="HMTEST1009",
            guest="Guest India",
            listing=DUNA,
            check_in=date(2026, 5, 26),
            check_out=date(2026, 6, 9),
            email_date=_at(2026, 5, 6),
            adults=2,
            other_guests=None,
            payout_lines=(
                ("Preço da acomodação para 14 noites", 2380.00),
                ("Taxa de limpeza", 120.00),
                ("Ajuste de preço por noite", -84.00),
                (_SERVICE_FEE, -86.83),
            ),
        ),
        {
            "accommodation": 2380.00,
            "cleaning_fee": 120.00,
            "discount": 84.00,
            "host_service_fee": 86.83,
        },
    ),
    ConfirmationScenario(
        "total_price",
        "Accommodation labelled 'Preço total da estadia' instead of a per-night price.",
        ConfirmationSpec(
            code="HMTEST1010",
            guest="Guest Juliett",
            listing=AURORA,
            check_in=date(2026, 6, 5),
            check_out=date(2026, 6, 7),
            email_date=_at(2026, 5, 25),
            adults=2,
            other_guests=None,
            payout_lines=(
                ("Preço total da estadia", 530.00),
                ("Taxa de limpeza", 90.00),
                (_SERVICE_FEE, -26.41),
            ),
        ),
        {"accommodation": 530.00, "cleaning_fee": 90.00, "host_service_fee": 26.41},
    ),
    ConfirmationScenario(
        "new_listing_promotion",
        "One adult, new-listing promotion as a discount, nbsp amounts with thousands.",
        ConfirmationSpec(
            code="HMTEST1011",
            guest="Guest Kilo",
            listing=FENIX,
            check_in=date(2026, 11, 15),
            check_out=date(2026, 12, 14),
            email_date=_at(2026, 10, 4),
            adults=1,
            other_guests=None,
            payout_lines=(
                ("Preço da acomodação para 29 noites", 5307.00),
                ("Taxa de limpeza", 140.00),
                ("Promoção para anúncios novos", -1044.00),
                ("Taxa de serviço do anfitrião (15.5% + IVA)", -669.04),
            ),
            money_sep="\xa0",
        ),
        {
            "accommodation": 5307.00,
            "cleaning_fee": 140.00,
            "discount": 1044.00,
            "host_service_fee": 669.04,
        },
    ),
    ConfirmationScenario(
        "no_cleaning_fee",
        "Only a total price and a 3% service fee: no cleaning line at all.",
        ConfirmationSpec(
            code="HMTEST1012",
            guest="Guest Lima",
            listing=BOREAL,
            check_in=date(2026, 6, 14),
            check_out=date(2026, 7, 31),
            email_date=_at(2026, 5, 30),
            adults=2,
            other_guests=None,
            payout_lines=(
                ("Preço total da estadia", 7140.00),
                ("Taxa de serviço do anfitrião (3.0% + IVA)", -263.47),
            ),
        ),
        {"accommodation": 7140.00, "host_service_fee": 263.47},
    ),
    ConfirmationScenario(
        "crosses_new_year",
        "Check-in in December without a year, checkout in January with one.",
        ConfirmationSpec(
            code="HMTEST1013",
            guest="Guest Mike",
            listing=CEDRO,
            check_in=date(2026, 12, 28),
            check_out=date(2027, 1, 2),
            email_date=_at(2026, 12, 2),
            adults=2,
            other_guests=None,
            payout_lines=(
                ("Preço da acomodação para 5 noites", 1450.00),
                ("Taxa de limpeza", 110.00),
                (_SERVICE_FEE, -66.61),
            ),
        ),
        {"accommodation": 1450.00, "cleaning_fee": 110.00, "host_service_fee": 66.61},
    ),
)


CANCELLATIONS: tuple[CancellationScenario, ...] = (
    CancellationScenario(
        "penalty_compact",
        "Penalty written 'R$315,40', with the 'Emitir reembolso integral' button below it.",
        CancellationSpec(
            code="HMTEST2001",
            guest="Guest November",
            listing=BOREAL,
            check_in=date(2026, 9, 4),
            check_out=date(2026, 9, 7),
            email_date=_at(2026, 8, 12, 14, 30),
            num_guests=2,
            penalty=315.40,
        ),
        "penalty",
        315.40,
        "BRL",
    ),
    CancellationScenario(
        "penalty_nbsp",
        "Penalty written 'R$\\xa0287,15' (the newer format).",
        CancellationSpec(
            code="HMTEST2002",
            guest="Guest Oscar",
            listing=DUNA,
            check_in=date(2026, 9, 18),
            check_out=date(2026, 9, 21),
            email_date=_at(2026, 8, 20, 18, 5),
            num_guests=3,
            penalty=287.15,
            money_sep="\xa0",
        ),
        "penalty",
        287.15,
        "BRL",
    ),
    CancellationScenario(
        "penalty_usd",
        "USD penalty: '$\\xa01.418,70\\xa0USD', where the suffix decides the currency.",
        CancellationSpec(
            code="HMTEST2003",
            guest="Guest Papa",
            listing=ECLIPSE,
            check_in=date(2026, 5, 4),
            check_out=date(2026, 6, 22),
            email_date=_at(2026, 4, 10, 11, 45),
            num_guests=2,
            penalty=1418.70,
            currency="USD",
            money_sep="\xa0",
        ),
        "penalty",
        1418.70,
        "USD",
    ),
    CancellationScenario(
        "full_refund",
        "The guest got a full refund: no amount and no refund button.",
        CancellationSpec(
            code="HMTEST2004",
            guest="Guest Quebec",
            listing=AURORA,
            check_in=date(2026, 8, 14),
            check_out=date(2026, 8, 16),
            email_date=_at(2026, 7, 29, 8, 15),
            num_guests=1,
            penalty=None,
        ),
        "full_refund",
        None,
        None,
    ),
    CancellationScenario(
        "full_refund_no_subject",
        "No subject header: the code comes from the wrapped body sentence.",
        CancellationSpec(
            code="HMTEST2005",
            guest="Guest Romeo",
            listing=FENIX,
            check_in=date(2026, 6, 19),
            check_out=date(2026, 6, 21),
            email_date=_at(2026, 6, 2, 16, 50),
            num_guests=2,
            penalty=None,
            with_subject=False,
        ),
        "full_refund",
        None,
        None,
    ),
)

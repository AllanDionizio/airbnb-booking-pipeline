-- Listings and owner contracts for the demo inbox (data/sample_inbox/).
-- `make demo` loads this into an empty database before the first run.
--
-- Two gaps are on purpose, so the review queue has something in it:
--   - Flat Fênix 606 is not registered (QUEUED_UNKNOWN_LISTING);
--   - the contract of Apartamento Duna 304 ends on 2026-12-01, before a
--     December stay (QUEUED_NO_CONTRACT).

insert into pipeline.listings (title) values
    ('Loft Aurora 101'),
    ('Studio Boreal 202'),
    ('Casa Cedro'),
    ('Apartamento Duna 304'),
    ('Studio Eclipse 505');

insert into pipeline.owner_contracts (listing_id, commission_pct, valid_from, valid_to)
select
    listing_id,
    case title when 'Casa Cedro' then 18.00 else 20.00 end,
    date '2026-01-01',
    case title when 'Apartamento Duna 304' then date '2026-12-01' end
from pipeline.listings;

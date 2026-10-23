-- upsert_booking(payload jsonb) → jsonb
--
-- Turns one parsed confirmation email into a booking, or into a review-queue
-- item when a person has to step in. Safe to call any number of times with
-- the same email.
--
-- Payload (built by load/postgres.py):
--   {"message_id": "...", "platform": "airbnb", "confirmation_code": "...",
--    "listing_title": "...", "guest_name": "...", "num_guests": 2,
--    "check_in": "YYYY-MM-DD", "check_out": "YYYY-MM-DD",
--    "payout_lines": {"accommodation": 480.0, "cleaning_fee": 90.0, ...},
--    "original_currency": "BRL" | "USD",
--    "fx": null | {"rate": 5.1234, "quoted_on": "YYYY-MM-DD", "source": "ptax"}}
--
-- Checks, in order. The first that applies decides the result code:
--   1. malformed payload                    → INVALID_PAYLOAD (nothing written)
--   2. message already processed            → ALREADY_PROCESSED (earlier outcome)
--   3. listing title not registered         → QUEUED_UNKNOWN_LISTING
--   4. no contract in force at check-in     → QUEUED_NO_CONTRACT
--   5. code exists and is no longer draft   → ALREADY_EXISTS (left untouched)
--   6. dates clash with a live booking      → QUEUED_OVERLAP
--   7. code exists as draft                 → UPDATED
--      otherwise                            → CREATED
--
-- Every outcome except INVALID_PAYLOAD is recorded in processed_messages.

create or replace function pipeline._record(
    p_message_id text,
    p_code       text,
    p_booking_id bigint,
    p_review_id  bigint,
    p_message    text
) returns jsonb
language sql
set search_path = pipeline, pg_temp
as $$
    insert into processed_messages (message_id, result_code, booking_id, review_id)
    values (p_message_id, p_code, p_booking_id, p_review_id);

    select jsonb_build_object(
        'code', p_code,
        'booking_id', p_booking_id,
        'review_id', p_review_id,
        'message', p_message
    );
$$;


create or replace function pipeline._enqueue(
    p           jsonb,
    p_reason    text,
    p_detail    text,
    p_conflicts bigint[] default null
) returns bigint
language sql
set search_path = pipeline, pg_temp
as $$
    insert into booking_review_queue (
        reason, detail, platform, confirmation_code, listing_title,
        check_in, check_out, conflicting_booking_ids, payload, source_message_id
    )
    values (
        p_reason, p_detail, p->>'platform', p->>'confirmation_code', p->>'listing_title',
        (p->>'check_in')::date, (p->>'check_out')::date, p_conflicts, p, p->>'message_id'
    )
    returning review_id;
$$;


create or replace function pipeline.upsert_booking(p jsonb)
returns jsonb
language plpgsql
set search_path = pipeline, pg_temp
as $$
declare
    v_message_id  text := nullif(btrim(p->>'message_id'), '');
    v_platform    text := nullif(btrim(p->>'platform'), '');
    v_code        text := nullif(btrim(p->>'confirmation_code'), '');
    v_title       text := nullif(btrim(p->>'listing_title'), '');
    v_currency    text := p->>'original_currency';
    v_lines       jsonb := coalesce(p->'payout_lines', '{}'::jsonb);
    v_fx          jsonb := nullif(p->'fx', 'null'::jsonb);
    v_check_in    date;
    v_check_out   date;
    v_prior       processed_messages%rowtype;
    v_listing_id  bigint;
    v_contract_id bigint;
    v_existing    bookings%rowtype;
    v_conflicts   bigint[];
    v_booking_id  bigint;
    v_review_id   bigint;
begin
    -- 1. Malformed payload: a loader bug, not something the queue can fix.
    -- Not recorded, so the same email can be retried once the bug is fixed.
    begin
        v_check_in := (p->>'check_in')::date;
        v_check_out := (p->>'check_out')::date;
        perform (p->>'num_guests')::integer;
        perform value::numeric from jsonb_each_text(v_lines);
    exception when others then
        v_check_in := null;  -- any bad date or number fails the check below
    end;
    if v_message_id is null or v_platform is null or v_code is null
       or nullif(btrim(p->>'guest_name'), '') is null
       or v_check_in is null or v_check_out is null or v_check_out <= v_check_in
       or coalesce((v_lines->>'accommodation')::numeric, 0) <= 0
       or exists (
           select 1 from jsonb_object_keys(v_lines) as k
           where k not in ('accommodation', 'cleaning_fee', 'pet_fee',
                           'other_income', 'discount', 'host_service_fee')
       )
       or v_currency is null or v_currency not in ('BRL', 'USD')
       or (v_currency = 'USD') <> (v_fx is not null)
    then
        return jsonb_build_object(
            'code', 'INVALID_PAYLOAD',
            'booking_id', null,
            'review_id', null,
            'message', 'Payload is missing required fields or has inconsistent values.'
        );
    end if;

    -- 2. Idempotency by email
    select * into v_prior from processed_messages where message_id = v_message_id;
    if found then
        return jsonb_build_object(
            'code', 'ALREADY_PROCESSED',
            'booking_id', v_prior.booking_id,
            'review_id', v_prior.review_id,
            'message', format('Message already processed (%s).', v_prior.result_code)
        );
    end if;

    -- 3. Listing
    select listing_id into v_listing_id
    from listings
    where platform = v_platform and lower(btrim(title)) = lower(v_title);

    if v_listing_id is null then
        v_review_id := _enqueue(p, 'unknown_listing',
            format('No listing registered with the title %L.', v_title));
        return _record(v_message_id, 'QUEUED_UNKNOWN_LISTING', null, v_review_id,
            'Listing not found; sent to review.');
    end if;

    -- 4. Contract in force on the check-in date
    select contract_id into v_contract_id
    from owner_contracts
    where listing_id = v_listing_id
      and daterange(valid_from, valid_to) @> v_check_in;

    if v_contract_id is null then
        v_review_id := _enqueue(p, 'no_contract',
            format('Listing %s has no contract in force on %s.', v_listing_id, v_check_in));
        return _record(v_message_id, 'QUEUED_NO_CONTRACT', null, v_review_id,
            'No contract in force at check-in; sent to review.');
    end if;

    -- 5. Same booking seen before. Once a person took it out of draft, the
    -- pipeline does not overwrite it.
    select * into v_existing
    from bookings
    where platform = v_platform and confirmation_code = v_code
    for update;

    if found and v_existing.status <> 'draft' then
        return _record(v_message_id, 'ALREADY_EXISTS', v_existing.booking_id, null,
            format('Booking exists with status %L; left untouched.', v_existing.status));
    end if;

    -- 6. Overlap with a live booking of the same listing. Ranges are
    -- half-open, so a checkout and a check-in on the same day do not clash.
    select array_agg(booking_id order by booking_id) into v_conflicts
    from bookings
    where listing_id = v_listing_id
      and status <> 'cancelled'
      and booking_id is distinct from v_existing.booking_id
      and daterange(check_in, check_out) && daterange(v_check_in, v_check_out);

    if v_conflicts is not null then
        v_review_id := _enqueue(p, 'overlap',
            format('Dates %s to %s clash with booking(s) %s.',
                   v_check_in, v_check_out, array_to_string(v_conflicts, ', ')),
            v_conflicts);
        return _record(v_message_id, 'QUEUED_OVERLAP', null, v_review_id,
            'Dates clash with a live booking; sent to review.');
    end if;

    -- 7. Write
    if v_existing.booking_id is not null then
        update bookings set
            listing_id        = v_listing_id,
            contract_id       = v_contract_id,
            guest_name        = p->>'guest_name',
            check_in          = v_check_in,
            check_out         = v_check_out,
            num_guests        = (p->>'num_guests')::integer,
            accommodation     = (v_lines->>'accommodation')::numeric,
            cleaning_fee      = coalesce((v_lines->>'cleaning_fee')::numeric, 0),
            pet_fee           = coalesce((v_lines->>'pet_fee')::numeric, 0),
            other_income      = coalesce((v_lines->>'other_income')::numeric, 0),
            discount          = coalesce((v_lines->>'discount')::numeric, 0),
            host_service_fee  = coalesce((v_lines->>'host_service_fee')::numeric, 0),
            original_currency = v_currency,
            fx_rate           = (v_fx->>'rate')::numeric,
            fx_quoted_on      = (v_fx->>'quoted_on')::date,
            fx_source         = v_fx->>'source',
            last_message_id   = v_message_id,
            updated_at        = now()
        where booking_id = v_existing.booking_id;

        return _record(v_message_id, 'UPDATED', v_existing.booking_id, null,
            'Draft booking updated.');
    end if;

    insert into bookings (
        platform, confirmation_code, listing_id, contract_id, guest_name,
        check_in, check_out, num_guests,
        accommodation, cleaning_fee, pet_fee, other_income, discount, host_service_fee,
        original_currency, fx_rate, fx_quoted_on, fx_source, last_message_id
    )
    values (
        v_platform, v_code, v_listing_id, v_contract_id, p->>'guest_name',
        v_check_in, v_check_out, (p->>'num_guests')::integer,
        (v_lines->>'accommodation')::numeric,
        coalesce((v_lines->>'cleaning_fee')::numeric, 0),
        coalesce((v_lines->>'pet_fee')::numeric, 0),
        coalesce((v_lines->>'other_income')::numeric, 0),
        coalesce((v_lines->>'discount')::numeric, 0),
        coalesce((v_lines->>'host_service_fee')::numeric, 0),
        v_currency, (v_fx->>'rate')::numeric, (v_fx->>'quoted_on')::date, v_fx->>'source',
        v_message_id
    )
    returning booking_id into v_booking_id;

    return _record(v_message_id, 'CREATED', v_booking_id, null, 'Draft booking created.');
end;
$$;

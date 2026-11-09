-- Additive upgrade of the UPLOADED Supabase schema. No existing rows are deleted.
-- Apply supabase_schema.sql first on fresh projects. Review/back up before use.
begin;
alter table public.listings
  add column if not exists item_details jsonb,
  add column if not exists wanted_food text,
  add column if not exists wanted_quantity numeric,
  add column if not exists wanted_unit text,
  add column if not exists wanted_items jsonb,
  add column if not exists pickup_start timestamptz,
  add column if not exists pickup_end timestamptz,
  add column if not exists timezone text default 'America/Los_Angeles',
  add column if not exists eligibility_confirmed boolean default false,
  add column if not exists storage_confirmed boolean default false,
  add column if not exists reservation_id uuid,
  add column if not exists inedible_confirmed boolean default false,
  add column if not exists photo_id uuid,
  add column if not exists photo_sha256 text,
  add column if not exists bob_review_id uuid,
  add column if not exists bob_reviewed_at timestamptz,
  add column if not exists is_test boolean not null default false;
alter table public.listings drop constraint if exists listings_status_check;
alter table public.listings add constraint listings_status_check
  check (status in ('open','reserved','traded','given_away','donated','expired','withdrawn','composted'));
alter table public.trade_offers
  add column if not exists give_items jsonb,
  add column if not exists receive_items jsonb,
  add column if not exists pickup_start timestamptz,
  add column if not exists pickup_end timestamptz,
  add column if not exists location text;
alter table public.food_requests
  add column if not exists quantity numeric,
  add column if not exists unit text,
  add column if not exists pickup_start timestamptz,
  add column if not exists pickup_end timestamptz,
  add column if not exists completed_by uuid[] not null default '{}',
  add column if not exists accepted_by uuid[] not null default '{}',
  add column if not exists item_details jsonb;
alter table public.food_requests drop constraint if exists food_requests_status_check;
alter table public.food_requests add constraint food_requests_status_check
  check (status in ('open','expired','pending','approved','declined','completion_pending','completed'));
alter table public.impact_records
  add column if not exists item_name text,
  add column if not exists quantity numeric,
  add column if not exists unit text,
  add column if not exists identity text,
  add column if not exists destination_id text,
  add column if not exists confirmation_type text,
  add column if not exists is_test boolean not null default false;
alter table public.impact_records drop constraint if exists impact_records_action_check;
alter table public.impact_records add constraint impact_records_action_check
  check (action in ('exchange','giveaway','donation','compost'));
create unique index if not exists impact_identity_unique on public.impact_records(identity) where identity is not null;
create table if not exists public.listing_photos (
  id uuid primary key,
  user_id uuid not null references public.user_profiles(id),
  sha256 text not null,
  state text not null check (state in ('active','deleted')),
  created_at timestamptz not null
);
alter table public.listing_photos enable row level security;
revoke all on public.listing_photos from anon,authenticated;
grant select,insert,update,delete on public.listing_photos to service_role;
create table if not exists public.listing_approvals (
  id uuid primary key,
  user_id uuid not null references public.user_profiles(id),
  listing_digest text not null,
  photo_id uuid not null,
  photo_sha256 text not null,
  policy text not null,
  approved_at timestamptz not null,
  expires_at timestamptz not null,
  used_listing_id uuid,
  check (expires_at > approved_at)
);
alter table public.listing_approvals enable row level security;
revoke all on public.listing_approvals from anon,authenticated;
grant select,insert,update,delete on public.listing_approvals to service_role;
create unique index if not exists listing_review_unique on public.listings(bob_review_id) where bob_review_id is not null;
create table if not exists public.echopantry_revision (
  id integer primary key check (id=1),
  revision bigint not null default 0
);
insert into public.echopantry_revision(id) values (1) on conflict do nothing;
alter table public.echopantry_revision enable row level security;
grant select,update on public.echopantry_revision to service_role;

-- One SQL statement provides a consistent snapshot. Data stays server-side.
create or replace function public.echopantry_snapshot() returns jsonb
language sql stable security invoker set search_path = public
as $$
 select jsonb_build_object(
   'schema_version', 4,
   'revision', (select revision from echopantry_revision where id=1),
   'active_users', coalesce((select jsonb_agg(id) from user_profiles where deleted_at is null),'[]'),
   'listings', coalesce((select jsonb_agg(to_jsonb(t)) from listings t),'[]'),
   'trade_offers', coalesce((select jsonb_agg(to_jsonb(t)) from trade_offers t),'[]'),
   'food_requests', coalesce((select jsonb_agg(to_jsonb(t)) from food_requests t),'[]'),
   'notifications', coalesce((select jsonb_agg(to_jsonb(t)) from notifications t),'[]'),
   'ratings', coalesce((select jsonb_agg(to_jsonb(t)) from ratings t),'[]'),
   'reports', coalesce((select jsonb_agg(to_jsonb(t)) from reports t),'[]'),
   'user_blocks', coalesce((select jsonb_agg(to_jsonb(t)) from user_blocks t),'[]'),
   'impact_records', coalesce((select jsonb_agg(to_jsonb(t)) from impact_records t),'[]'),
   'listing_approvals', coalesce((select jsonb_agg(to_jsonb(t)) from listing_approvals t),'[]'),
   'listing_photos', coalesce((select jsonb_agg(to_jsonb(t)) from listing_photos t),'[]')
 );
$$;

-- Global optimistic lock is deliberately simple for a small neighborhood pilot.
-- All writes from a request either commit together or roll back together.
-- Concurrent final acceptances cannot commit from the same availability snapshot.
create or replace function public.echopantry_commit(p_revision bigint, p_writes jsonb)
returns bigint language plpgsql security invoker set search_path=public
as $$
declare
  current_revision bigint;
  entry jsonb;
  row_data jsonb;
  target_table text;
  columns_sql text;
  updates_sql text;
begin
  select revision into current_revision from echopantry_revision where id=1 for update;
  if current_revision is null or current_revision <> p_revision then
    raise exception 'EP_CONFLICT';
  end if;
  if jsonb_typeof(p_writes) <> 'array' or jsonb_array_length(p_writes) > 100 then
    raise exception 'EP_INVALID_TRANSACTION';
  end if;
  for entry in select value from jsonb_array_elements(p_writes) loop
    target_table := entry->>'table';
    row_data := entry->'row';
    if target_table not in ('listings','trade_offers','food_requests','notifications',
                          'ratings','reports','user_blocks','impact_records','listing_approvals','listing_photos') then
      raise exception 'EP_INVALID_TABLE';
    end if;
    -- Identifiers are quoted and constrained to existing columns.
    select string_agg(format('%I',key),',' order by key),
           string_agg(format('%I=excluded.%I',key,key),',' order by key)
      into columns_sql,updates_sql
      from jsonb_object_keys(row_data) as key
      where exists (select 1 from information_schema.columns c
                    where c.table_schema='public' and c.table_name=target_table
                    and c.column_name=key);
    if columns_sql is null then raise exception 'EP_INVALID_ROW'; end if;
    execute format(
      'insert into public.%I (%s) select %s from jsonb_populate_record(null::public.%I,$1) on conflict(id) do update set %s',
      target_table,columns_sql,columns_sql,target_table,updates_sql) using row_data;
  end loop;
  update echopantry_revision set revision=revision+1 where id=1 returning revision into current_revision;
  return current_revision;
end;
$$;
revoke all on function public.echopantry_snapshot() from public,anon,authenticated;
revoke all on function public.echopantry_commit(bigint,jsonb) from public,anon,authenticated;
grant execute on function public.echopantry_snapshot() to service_role;
grant execute on function public.echopantry_commit(bigint,jsonb) to service_role;
notify pgrst, 'reload schema';
commit;

-- The result below confirms the update, rather than only "No rows returned".
select
  public.echopantry_snapshot()->>'schema_version' as schema_version,
  to_regclass('public.listing_photos') as photo_table,
  to_regclass('public.listing_approvals') as approval_table,
  exists (select 1 from information_schema.columns
          where table_schema='public' and table_name='listings' and column_name='wanted_items') as wanted_items_column;

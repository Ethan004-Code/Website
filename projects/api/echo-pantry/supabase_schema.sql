-- Run in the Supabase SQL editor, then restart Flask. Safe to rerun.
-- Existing app_users/bundles/items/trades data is preserved, but not imported.
-- Flask uses service_role; browsers access data through the authenticated API.
begin;

-- Preserve the incompatible ratings table from the original entity diagram.
do $$
begin
    if exists (
        select 1 from information_schema.columns
        where table_schema = 'public' and table_name = 'ratings'
          and column_name = 'trade_id'
    ) then
        if to_regclass('public.legacy_ratings') is not null then
            raise exception 'legacy_ratings already exists; resolve the legacy table names before rerunning';
        end if;
        alter table public.ratings rename to legacy_ratings;
        alter table public.legacy_ratings enable row level security;
    end if;
end $$;

create table if not exists public.user_profiles (
    id uuid primary key references auth.users(id),
    auth_user_id uuid unique references auth.users(id),
    email text unique,
    display_name text not null,
    average_rating numeric(3,2) not null default 0 check (average_rating between 0 and 5),
    completed_trades integer not null default 0 check (completed_trades >= 0),
    total_items_saved integer not null default 0 check (total_items_saved >= 0),
    created_at timestamptz not null default now(),
    last_login_at timestamptz,
    deleted_at timestamptz
);

create table if not exists public.listings (
    id uuid primary key,
    user_id uuid not null references public.user_profiles(id),
    name text not null,
    mode text not null check (mode in ('exchange', 'giveaway')),
    listing_type text not null check (listing_type in ('single', 'bundle')),
    items text[] not null,
    quantities jsonb not null default '{}'::jsonb,
    wants text[] not null default '{}'::text[],
    deadline timestamptz not null,
    best_by timestamptz,
    location text not null,
    availability text not null,
    allergens text not null default 'Not specified',
    rating numeric(3,2) check (rating between 0 and 5),
    trades integer not null default 0 check (trades >= 0),
    status text not null default 'open'
        check (status in ('open', 'reserved', 'traded', 'given_away', 'donated', 'expired')),
    created_at timestamptz not null default now()
);

create table if not exists public.trade_offers (
    id uuid primary key,
    listing_id uuid not null references public.listings(id),
    match_id uuid not null references public.listings(id),
    give text[] not null default '{}'::text[],
    receive text[] not null default '{}'::text[],
    participants jsonb not null default '[]'::jsonb,
    accepted_by uuid[] not null default '{}'::uuid[],
    completed_by uuid[] not null default '{}'::uuid[],
    declined_by uuid references public.user_profiles(id),
    status text not null default 'pending'
        check (status in ('pending', 'awaiting_second_acceptance', 'confirmed',
                          'declined', 'completion_pending', 'completed')),
    created_at timestamptz not null default now()
);

-- General requests use user_id/items; giveaway requests use listing_id/requester_id.
create table if not exists public.food_requests (
    id uuid primary key,
    user_id uuid references public.user_profiles(id),
    name text,
    items text[] not null default '{}'::text[],
    quantities jsonb not null default '{}'::jsonb,
    offer_items text[] not null default '{}'::text[],
    deadline timestamptz,
    deadline_text text,
    natural_language_text text,
    pickup_availability text,
    location text,
    listing_id uuid references public.listings(id),
    requester_id uuid references public.user_profiles(id),
    owner_id uuid references public.user_profiles(id),
    requester_name text,
    status text not null
        check (status in ('open', 'expired', 'pending', 'approved', 'declined', 'completed')),
    created_at timestamptz not null default now()
);

create table if not exists public.notifications (
    id uuid primary key,
    user_id uuid not null references public.user_profiles(id),
    kind text not null,
    message text not null,
    -- May identify a listing, trade offer, or giveaway request.
    entity_id uuid not null,
    read boolean not null default false,
    created_at timestamptz not null default now()
);

create table if not exists public.ratings (
    id uuid primary key,
    offer_id uuid references public.trade_offers(id),
    request_id uuid references public.food_requests(id),
    from_user_id uuid not null references public.user_profiles(id),
    to_user_id uuid not null references public.user_profiles(id),
    stars integer not null check (stars between 1 and 5),
    accurate_description integer not null check (accurate_description between 1 and 5),
    reliable_pickup integer not null check (reliable_pickup between 1 and 5),
    respectful_exchange integer not null check (respectful_exchange between 1 and 5),
    created_at timestamptz not null default now(),
    check (from_user_id <> to_user_id),
    check (offer_id is not null or request_id is not null),
    unique (offer_id, from_user_id),
    unique (request_id, from_user_id)
);

create table if not exists public.reports (
    id uuid primary key,
    reporter_id uuid not null references public.user_profiles(id),
    reported_user_id uuid not null references public.user_profiles(id),
    reason text not null,
    created_at timestamptz not null default now()
);

create table if not exists public.user_blocks (
    id uuid primary key,
    user_id uuid not null references public.user_profiles(id),
    blocked_user_id uuid not null references public.user_profiles(id),
    created_at timestamptz not null default now(),
    check (user_id <> blocked_user_id),
    unique (user_id, blocked_user_id)
);

create table if not exists public.impact_records (
    id uuid primary key,
    listing_id uuid not null references public.listings(id),
    offer_id uuid references public.trade_offers(id),
    user_id uuid not null references public.user_profiles(id),
    action text not null check (action in ('exchange', 'giveaway', 'donation')),
    item_count integer not null check (item_count >= 0),
    created_at timestamptz not null default now()
);

create index if not exists listings_status_deadline_idx on public.listings(status, deadline);
create index if not exists listings_user_id_idx on public.listings(user_id);
create index if not exists trade_offers_listing_id_idx on public.trade_offers(listing_id);
create index if not exists trade_offers_match_id_idx on public.trade_offers(match_id);
create index if not exists food_requests_user_id_idx on public.food_requests(user_id);
create index if not exists food_requests_requester_id_idx on public.food_requests(requester_id);
create index if not exists food_requests_owner_id_idx on public.food_requests(owner_id);
create index if not exists notifications_user_id_idx on public.notifications(user_id);
create index if not exists ratings_to_user_id_idx on public.ratings(to_user_id);

-- No browser-facing policies: Flask checks Auth sessions and uses service_role.
alter table public.user_profiles enable row level security;
alter table public.listings enable row level security;
alter table public.trade_offers enable row level security;
alter table public.food_requests enable row level security;
alter table public.notifications enable row level security;
alter table public.ratings enable row level security;
alter table public.reports enable row level security;
alter table public.user_blocks enable row level security;
alter table public.impact_records enable row level security;

grant usage on schema public to service_role;
grant select, insert, update, delete on table
    public.user_profiles, public.listings, public.trade_offers,
    public.food_requests, public.notifications, public.ratings,
    public.reports, public.user_blocks, public.impact_records to service_role;

commit;

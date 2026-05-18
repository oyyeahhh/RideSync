-- CarpoolSync canonical Postgres schema
-- Run this once in Supabase Dashboard → SQL Editor → New query → paste → Run.
-- Re-running is safe: every CREATE uses IF NOT EXISTS.

-- ─── extensions ───────────────────────────────────────────────────────────────
create extension if not exists "uuid-ossp";

-- ─── groups (top-level tenancy unit) ──────────────────────────────────────────
create table if not exists public.groups (
  id              text primary key,            -- "grp_xxxxxxxx"
  name            text not null,
  display_token   text unique,                 -- public URL for /display/<token>
  config          jsonb not null default '{}'::jsonb,  -- trip_config blob
  created_at      timestamptz not null default now()
);

-- ─── users (app accounts; may link to auth.users via supabase_uid) ────────────
create table if not exists public.users (
  id              text primary key,            -- "user_xxxxxxxx"
  supabase_uid    uuid references auth.users(id) on delete set null,
  email           text not null,
  password_hash   text,                        -- bcrypt; nullable if magic-link only
  name            text not null default '',
  phone           text,
  role            text not null default 'parent',  -- 'admin' | 'parent'
  family_id       text,
  group_id        text references public.groups(id) on delete cascade,
  child_name      text,
  address         text,
  joined_at       timestamptz not null default now(),
  calendar_token  text unique                  -- for cookieless /calendar/<token>.ics
);
create unique index if not exists users_email_lower_idx on public.users (lower(email));
create index if not exists users_group_idx on public.users (group_id);

-- ─── families (units within a group) ──────────────────────────────────────────
create table if not exists public.families (
  id                    text primary key,      -- "fam_xxxxxxxx"
  group_id              text not null references public.groups(id) on delete cascade,
  name                  text not null,
  primary_address       text,
  primary_address_lat   double precision,
  primary_address_lng   double precision,
  created_at            timestamptz not null default now()
);
create index if not exists families_group_idx on public.families (group_id);

-- ─── guardians (parents within a family) ──────────────────────────────────────
create table if not exists public.guardians (
  id          text primary key,
  family_id   text not null references public.families(id) on delete cascade,
  name        text not null,
  phone       text,
  email       text,
  is_driver   boolean not null default true
);
create index if not exists guardians_family_idx on public.guardians (family_id);

-- ─── kids (children within a family) ──────────────────────────────────────────
create table if not exists public.kids (
  id          text primary key,
  family_id   text not null references public.families(id) on delete cascade,
  name        text not null
);
create index if not exists kids_family_idx on public.kids (family_id);

-- ─── rotation (driver cycle per group) ────────────────────────────────────────
create table if not exists public.rotation (
  group_id        text primary key references public.groups(id) on delete cascade,
  order_ids       text[] not null default '{}',
  current_index   integer not null default 0
);

-- ─── schedule (upcoming trips) ────────────────────────────────────────────────
create table if not exists public.schedule (
  id                          text primary key,            -- "uuid hex slice"
  series_id                   text,
  group_id                    text not null references public.groups(id) on delete cascade,
  trip_date                   date not null,
  arrival_time                text,                        -- "HH:MM"
  return_time                 text,
  destination_name            text,
  destination_address         text,
  driver_family_id            text,
  driver_name                 text,
  return_driver_family_id     text,
  return_driver_name          text,
  route_sent                  boolean not null default false,
  rotation_advanced           boolean not null default false
);
create index if not exists schedule_group_date_idx on public.schedule (group_id, trip_date);
create index if not exists schedule_series_idx on public.schedule (series_id);

-- ─── trips (history log) ──────────────────────────────────────────────────────
create table if not exists public.trips (
  id                  uuid primary key default uuid_generate_v4(),
  group_id            text not null references public.groups(id) on delete cascade,
  trip_date           date not null,
  driver_family_id    text,
  driver_name         text,
  miles               double precision not null default 0,
  minutes             integer not null default 0,
  pickup_family_ids   text[] not null default '{}',
  created_at          timestamptz not null default now()
);
create index if not exists trips_group_date_idx on public.trips (group_id, trip_date);

-- ─── absences (per-day, per-family flags) ─────────────────────────────────────
create table if not exists public.absences (
  group_id      text not null references public.groups(id) on delete cascade,
  absent_date   date not null,
  family_id     text not null,
  primary key (group_id, absent_date, family_id)
);

-- ─── karma (fairness ledger) ──────────────────────────────────────────────────
create table if not exists public.karma (
  group_id      text not null references public.groups(id) on delete cascade,
  family_id     text not null,
  family_name   text,
  requested     integer not null default 0,
  covered       integer not null default 0,
  primary key (group_id, family_id)
);

-- ─── invites (sign-up tokens) ─────────────────────────────────────────────────
create table if not exists public.invites (
  token         text primary key,
  phone         text,
  group_id      text references public.groups(id) on delete cascade,
  family_id     text,
  family_name   text,
  created_at    timestamptz not null default now(),
  used          boolean not null default false
);
create index if not exists invites_used_idx on public.invites (used, created_at);

-- ─── password resets ──────────────────────────────────────────────────────────
create table if not exists public.password_resets (
  token         text primary key,
  user_id       text not null references public.users(id) on delete cascade,
  created_at    timestamptz not null default now(),
  used          boolean not null default false
);

-- ─── geocode cache (global) ───────────────────────────────────────────────────
create table if not exists public.geocode_cache (
  address       text primary key,
  latitude      double precision not null,
  longitude     double precision not null,
  cached_at     timestamptz not null default now()
);

-- ─── route cache (latest computed pickup list per group) ──────────────────────
create table if not exists public.route_cache (
  group_id      text primary key references public.groups(id) on delete cascade,
  payload       jsonb not null,                           -- full cache blob
  updated_at    timestamptz not null default now()
);

-- ─── live driver location (per group, ephemeral but persisted) ────────────────
create table if not exists public.location (
  group_id      text primary key references public.groups(id) on delete cascade,
  active        boolean not null default false,
  driver_name   text,
  trip_leg      text,
  latitude      double precision,
  longitude     double precision,
  started_at    timestamptz,
  updated_at    timestamptz default now()
);

-- ─── confirmations + swap state (used by SMS webhook) ─────────────────────────
create table if not exists public.confirmations (
  group_id      text not null references public.groups(id) on delete cascade,
  phone         text not null,
  message       text,
  received_at   timestamptz not null default now(),
  primary key (group_id, phone)
);

create table if not exists public.swap_state (
  group_id      text primary key references public.groups(id) on delete cascade,
  state         jsonb not null default '{}'::jsonb,
  updated_at    timestamptz not null default now()
);


-- ─── Row-Level Security (DISABLED for now) ────────────────────────────────────
-- During Phase 2 we access everything via the service-role key, so RLS is
-- effectively bypassed. When we wire Supabase Auth fully in a later phase
-- we'll enable RLS and add per-group policies.
alter table public.groups          disable row level security;
alter table public.users           disable row level security;
alter table public.families        disable row level security;
alter table public.guardians       disable row level security;
alter table public.kids            disable row level security;
alter table public.rotation        disable row level security;
alter table public.schedule        disable row level security;
alter table public.trips           disable row level security;
alter table public.absences        disable row level security;
alter table public.karma           disable row level security;
alter table public.invites         disable row level security;
alter table public.password_resets disable row level security;
alter table public.geocode_cache   disable row level security;
alter table public.route_cache     disable row level security;
alter table public.location        disable row level security;
alter table public.confirmations   disable row level security;
alter table public.swap_state      disable row level security;

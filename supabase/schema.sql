-- Схема для сервиса заявок. Выполнить целиком: Supabase Dashboard → SQL Editor → New query → Run.
-- Скрипт идемпотентный: его можно запускать повторно.

-- 1. Таблица ---------------------------------------------------------------
create table if not exists public.tickets (
  id          uuid primary key default gen_random_uuid(),
  -- владелец заявки подставляется самой базой из JWT, клиент его не передаёт
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  title       text not null,
  description text not null,
  category    text not null,
  status      text not null default 'new',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),

  constraint tickets_title_len       check (char_length(btrim(title)) between 1 and 200),
  constraint tickets_description_len check (char_length(btrim(description)) between 1 and 5000),
  constraint tickets_category_valid  check (category in ('bot', 'integration', 'report', 'other')),
  constraint tickets_status_valid    check (status in ('new', 'in_progress', 'done'))
);

create index if not exists tickets_user_created_idx
  on public.tickets (user_id, created_at desc);

-- 2. Защита служебных полей -------------------------------------------------
-- Пользователь не может подделать дату создания, сменить владельца или id.
create or replace function public.tickets_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    new.created_at := now();
    new.updated_at := now();
  else
    new.id         := old.id;
    new.user_id    := old.user_id;
    new.created_at := old.created_at;
    new.updated_at := now();
  end if;
  return new;
end;
$$;

drop trigger if exists tickets_guard on public.tickets;
create trigger tickets_guard
  before insert or update on public.tickets
  for each row execute function public.tickets_guard();

-- 3. Row Level Security -----------------------------------------------------
-- Ограничение работает в самой базе: любой запрос к REST API / PostgREST
-- (в том числе напрямую, мимо нашего приложения) видит только свои строки.
alter table public.tickets enable row level security;

drop policy if exists tickets_select_own on public.tickets;
create policy tickets_select_own on public.tickets
  for select to authenticated
  using ((select auth.uid()) = user_id);

drop policy if exists tickets_insert_own on public.tickets;
create policy tickets_insert_own on public.tickets
  for insert to authenticated
  with check ((select auth.uid()) = user_id and status = 'new');

drop policy if exists tickets_update_own on public.tickets;
create policy tickets_update_own on public.tickets
  for update to authenticated
  using      ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

-- 4. Права ролей ------------------------------------------------------------
-- Анонимный доступ закрыт полностью, удаление заявок не предусмотрено.
revoke all on public.tickets from anon, authenticated;
grant select, insert, update on public.tickets to authenticated;

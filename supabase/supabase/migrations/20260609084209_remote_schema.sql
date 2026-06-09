drop extension if exists "pg_net";

create extension if not exists "vector" with schema "public";


  create table "public"."chunks" (
    "id" uuid not null default gen_random_uuid(),
    "org_id" uuid not null,
    "document_id" uuid not null,
    "content" text not null,
    "content_tsv" tsvector generated always as (to_tsvector('english'::regconfig, content)) stored,
    "chunk_index" integer not null,
    "page_number" integer,
    "section_heading" text,
    "token_count" integer,
    "metadata" jsonb not null default '{}'::jsonb
      );


alter table "public"."chunks" enable row level security;


  create table "public"."conversations" (
    "id" uuid not null default gen_random_uuid(),
    "org_id" uuid not null,
    "user_id" uuid not null,
    "title" text,
    "created_at" timestamp with time zone not null default now(),
    "updated_at" timestamp with time zone not null default now()
      );


alter table "public"."conversations" enable row level security;


  create table "public"."documents" (
    "id" uuid not null default gen_random_uuid(),
    "org_id" uuid not null,
    "name" text not null,
    "file_path" text not null,
    "file_type" text not null,
    "file_size_bytes" integer,
    "status" text not null default 'pending'::text,
    "chunk_count" integer,
    "metadata" jsonb not null default '{}'::jsonb,
    "created_by" uuid,
    "created_at" timestamp with time zone not null default now()
      );


alter table "public"."documents" enable row level security;


  create table "public"."embeddings" (
    "id" uuid not null default gen_random_uuid(),
    "chunk_id" uuid not null,
    "org_id" uuid not null,
    "embedding" public.vector(768) not null
      );


alter table "public"."embeddings" enable row level security;


  create table "public"."messages" (
    "id" uuid not null default gen_random_uuid(),
    "conversation_id" uuid not null,
    "org_id" uuid not null,
    "role" text not null,
    "content" text not null,
    "sources" jsonb,
    "feedback" text,
    "created_at" timestamp with time zone not null default now()
      );


alter table "public"."messages" enable row level security;


  create table "public"."organizations" (
    "id" uuid not null default gen_random_uuid(),
    "name" text not null,
    "slug" text not null,
    "plan" text not null default 'free'::text,
    "created_at" timestamp with time zone not null default now()
      );


alter table "public"."organizations" enable row level security;


  create table "public"."users" (
    "id" uuid not null,
    "org_id" uuid not null,
    "role" text not null default 'member'::text,
    "display_name" text,
    "created_at" timestamp with time zone not null default now()
      );


alter table "public"."users" enable row level security;

CREATE UNIQUE INDEX chunks_pkey ON public.chunks USING btree (id);

CREATE UNIQUE INDEX conversations_pkey ON public.conversations USING btree (id);

CREATE UNIQUE INDEX documents_pkey ON public.documents USING btree (id);

CREATE UNIQUE INDEX embeddings_pkey ON public.embeddings USING btree (id);

CREATE INDEX idx_chunks_content_tsv ON public.chunks USING gin (content_tsv);

CREATE INDEX idx_chunks_document_id ON public.chunks USING btree (document_id);

CREATE INDEX idx_chunks_org_id ON public.chunks USING btree (org_id);

CREATE INDEX idx_conversations_user ON public.conversations USING btree (user_id, updated_at DESC);

CREATE INDEX idx_documents_org_created ON public.documents USING btree (org_id, created_at DESC);

CREATE INDEX idx_documents_org_status ON public.documents USING btree (org_id, status);

CREATE INDEX idx_embeddings_org_id ON public.embeddings USING btree (org_id);

CREATE INDEX idx_embeddings_vector ON public.embeddings USING ivfflat (embedding public.vector_cosine_ops) WITH (lists='100');

CREATE INDEX idx_messages_conversation ON public.messages USING btree (conversation_id, created_at);

CREATE UNIQUE INDEX messages_pkey ON public.messages USING btree (id);

CREATE UNIQUE INDEX organizations_pkey ON public.organizations USING btree (id);

CREATE UNIQUE INDEX organizations_slug_key ON public.organizations USING btree (slug);

CREATE UNIQUE INDEX users_pkey ON public.users USING btree (id);

alter table "public"."chunks" add constraint "chunks_pkey" PRIMARY KEY using index "chunks_pkey";

alter table "public"."conversations" add constraint "conversations_pkey" PRIMARY KEY using index "conversations_pkey";

alter table "public"."documents" add constraint "documents_pkey" PRIMARY KEY using index "documents_pkey";

alter table "public"."embeddings" add constraint "embeddings_pkey" PRIMARY KEY using index "embeddings_pkey";

alter table "public"."messages" add constraint "messages_pkey" PRIMARY KEY using index "messages_pkey";

alter table "public"."organizations" add constraint "organizations_pkey" PRIMARY KEY using index "organizations_pkey";

alter table "public"."users" add constraint "users_pkey" PRIMARY KEY using index "users_pkey";

alter table "public"."chunks" add constraint "chunks_document_id_fkey" FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE not valid;

alter table "public"."chunks" validate constraint "chunks_document_id_fkey";

alter table "public"."chunks" add constraint "chunks_org_id_fkey" FOREIGN KEY (org_id) REFERENCES public.organizations(id) ON DELETE CASCADE not valid;

alter table "public"."chunks" validate constraint "chunks_org_id_fkey";

alter table "public"."conversations" add constraint "conversations_org_id_fkey" FOREIGN KEY (org_id) REFERENCES public.organizations(id) ON DELETE CASCADE not valid;

alter table "public"."conversations" validate constraint "conversations_org_id_fkey";

alter table "public"."conversations" add constraint "conversations_user_id_fkey" FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE not valid;

alter table "public"."conversations" validate constraint "conversations_user_id_fkey";

alter table "public"."documents" add constraint "documents_created_by_fkey" FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL not valid;

alter table "public"."documents" validate constraint "documents_created_by_fkey";

alter table "public"."documents" add constraint "documents_file_type_check" CHECK ((file_type = ANY (ARRAY['pdf'::text, 'docx'::text, 'txt'::text, 'md'::text]))) not valid;

alter table "public"."documents" validate constraint "documents_file_type_check";

alter table "public"."documents" add constraint "documents_org_id_fkey" FOREIGN KEY (org_id) REFERENCES public.organizations(id) ON DELETE CASCADE not valid;

alter table "public"."documents" validate constraint "documents_org_id_fkey";

alter table "public"."documents" add constraint "documents_status_check" CHECK ((status = ANY (ARRAY['pending'::text, 'processing'::text, 'ready'::text, 'failed'::text]))) not valid;

alter table "public"."documents" validate constraint "documents_status_check";

alter table "public"."embeddings" add constraint "embeddings_chunk_id_fkey" FOREIGN KEY (chunk_id) REFERENCES public.chunks(id) ON DELETE CASCADE not valid;

alter table "public"."embeddings" validate constraint "embeddings_chunk_id_fkey";

alter table "public"."embeddings" add constraint "embeddings_org_id_fkey" FOREIGN KEY (org_id) REFERENCES public.organizations(id) ON DELETE CASCADE not valid;

alter table "public"."embeddings" validate constraint "embeddings_org_id_fkey";

alter table "public"."messages" add constraint "messages_conversation_id_fkey" FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE not valid;

alter table "public"."messages" validate constraint "messages_conversation_id_fkey";

alter table "public"."messages" add constraint "messages_feedback_check" CHECK ((feedback = ANY (ARRAY['positive'::text, 'negative'::text]))) not valid;

alter table "public"."messages" validate constraint "messages_feedback_check";

alter table "public"."messages" add constraint "messages_org_id_fkey" FOREIGN KEY (org_id) REFERENCES public.organizations(id) ON DELETE CASCADE not valid;

alter table "public"."messages" validate constraint "messages_org_id_fkey";

alter table "public"."messages" add constraint "messages_role_check" CHECK ((role = ANY (ARRAY['user'::text, 'assistant'::text]))) not valid;

alter table "public"."messages" validate constraint "messages_role_check";

alter table "public"."organizations" add constraint "organizations_plan_check" CHECK ((plan = ANY (ARRAY['free'::text, 'starter'::text, 'growth'::text, 'business'::text]))) not valid;

alter table "public"."organizations" validate constraint "organizations_plan_check";

alter table "public"."organizations" add constraint "organizations_slug_key" UNIQUE using index "organizations_slug_key";

alter table "public"."users" add constraint "users_id_fkey" FOREIGN KEY (id) REFERENCES auth.users(id) ON DELETE CASCADE not valid;

alter table "public"."users" validate constraint "users_id_fkey";

alter table "public"."users" add constraint "users_org_id_fkey" FOREIGN KEY (org_id) REFERENCES public.organizations(id) ON DELETE CASCADE not valid;

alter table "public"."users" validate constraint "users_org_id_fkey";

alter table "public"."users" add constraint "users_role_check" CHECK ((role = ANY (ARRAY['admin'::text, 'member'::text]))) not valid;

alter table "public"."users" validate constraint "users_role_check";

set check_function_bodies = off;

CREATE OR REPLACE FUNCTION public.auth_org_id()
 RETURNS uuid
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
  SELECT org_id FROM public.users WHERE id = auth.uid();
$function$
;

CREATE OR REPLACE FUNCTION public.fts_search(query_text text, match_org_id uuid, match_count integer DEFAULT 10, match_document_id uuid DEFAULT NULL::uuid)
 RETURNS TABLE(chunk_id uuid, content text, document_id uuid, document_name text, chunk_index integer, page_number integer, section_heading text, similarity double precision, snippet text)
 LANGUAGE plpgsql
 STABLE
 SET search_path TO 'public'
AS $function$
DECLARE
  tsq tsquery;
BEGIN
  tsq := websearch_to_tsquery('english', COALESCE(query_text, ''));

  -- Empty tsquery (no usable tokens) → return zero rows, no error.
  IF tsq IS NULL OR tsq::text = '' THEN
    RETURN;
  END IF;

  RETURN QUERY
  SELECT
    c.id                                                  AS chunk_id,
    c.content                                             AS content,
    c.document_id                                         AS document_id,
    d.name                                                AS document_name,
    c.chunk_index                                         AS chunk_index,
    c.page_number                                         AS page_number,
    c.section_heading                                     AS section_heading,
    ts_rank_cd(c.content_tsv, tsq, 32)::float             AS similarity,
    ts_headline(
      'english',
      c.content,
      tsq,
      'MaxFragments=2, MaxWords=18, MinWords=6, ShortWord=3, '
      || 'HighlightAll=false, StartSel=<mark>, StopSel=</mark>'
    )                                                     AS snippet
  FROM chunks c
  JOIN documents d ON d.id = c.document_id
  WHERE c.org_id = match_org_id
    AND (match_document_id IS NULL OR c.document_id = match_document_id)
    AND c.content_tsv @@ tsq
  ORDER BY ts_rank_cd(c.content_tsv, tsq, 32) DESC
  LIMIT match_count;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.vector_search(query_embedding public.vector, match_org_id uuid, match_count integer DEFAULT 10, match_document_id uuid DEFAULT NULL::uuid)
 RETURNS TABLE(chunk_id uuid, content text, document_id uuid, document_name text, chunk_index integer, page_number integer, section_heading text, similarity double precision)
 LANGUAGE plpgsql
 SET search_path TO 'public'
AS $function$
BEGIN
  -- Raises IVFFlat recall to ~95%. Scoped to this transaction only.
  SET LOCAL ivfflat.probes = 10;

  RETURN QUERY
  SELECT
    c.id                                              AS chunk_id,
    c.content                                         AS content,
    c.document_id                                     AS document_id,
    d.name                                            AS document_name,
    c.chunk_index                                     AS chunk_index,
    c.page_number                                     AS page_number,
    c.section_heading                                 AS section_heading,
    (1 - (e.embedding <=> query_embedding))::float    AS similarity
  FROM embeddings e
  JOIN chunks   c ON c.id = e.chunk_id
  JOIN documents d ON d.id = c.document_id
  WHERE e.org_id = match_org_id
    AND (match_document_id IS NULL OR c.document_id = match_document_id)
  ORDER BY e.embedding <=> query_embedding
  LIMIT match_count;
END;
$function$
;

grant delete on table "public"."chunks" to "anon";

grant insert on table "public"."chunks" to "anon";

grant references on table "public"."chunks" to "anon";

grant select on table "public"."chunks" to "anon";

grant trigger on table "public"."chunks" to "anon";

grant truncate on table "public"."chunks" to "anon";

grant update on table "public"."chunks" to "anon";

grant delete on table "public"."chunks" to "authenticated";

grant insert on table "public"."chunks" to "authenticated";

grant references on table "public"."chunks" to "authenticated";

grant select on table "public"."chunks" to "authenticated";

grant trigger on table "public"."chunks" to "authenticated";

grant truncate on table "public"."chunks" to "authenticated";

grant update on table "public"."chunks" to "authenticated";

grant delete on table "public"."chunks" to "service_role";

grant insert on table "public"."chunks" to "service_role";

grant references on table "public"."chunks" to "service_role";

grant select on table "public"."chunks" to "service_role";

grant trigger on table "public"."chunks" to "service_role";

grant truncate on table "public"."chunks" to "service_role";

grant update on table "public"."chunks" to "service_role";

grant delete on table "public"."conversations" to "anon";

grant insert on table "public"."conversations" to "anon";

grant references on table "public"."conversations" to "anon";

grant select on table "public"."conversations" to "anon";

grant trigger on table "public"."conversations" to "anon";

grant truncate on table "public"."conversations" to "anon";

grant update on table "public"."conversations" to "anon";

grant delete on table "public"."conversations" to "authenticated";

grant insert on table "public"."conversations" to "authenticated";

grant references on table "public"."conversations" to "authenticated";

grant select on table "public"."conversations" to "authenticated";

grant trigger on table "public"."conversations" to "authenticated";

grant truncate on table "public"."conversations" to "authenticated";

grant update on table "public"."conversations" to "authenticated";

grant delete on table "public"."conversations" to "service_role";

grant insert on table "public"."conversations" to "service_role";

grant references on table "public"."conversations" to "service_role";

grant select on table "public"."conversations" to "service_role";

grant trigger on table "public"."conversations" to "service_role";

grant truncate on table "public"."conversations" to "service_role";

grant update on table "public"."conversations" to "service_role";

grant delete on table "public"."documents" to "anon";

grant insert on table "public"."documents" to "anon";

grant references on table "public"."documents" to "anon";

grant select on table "public"."documents" to "anon";

grant trigger on table "public"."documents" to "anon";

grant truncate on table "public"."documents" to "anon";

grant update on table "public"."documents" to "anon";

grant delete on table "public"."documents" to "authenticated";

grant insert on table "public"."documents" to "authenticated";

grant references on table "public"."documents" to "authenticated";

grant select on table "public"."documents" to "authenticated";

grant trigger on table "public"."documents" to "authenticated";

grant truncate on table "public"."documents" to "authenticated";

grant update on table "public"."documents" to "authenticated";

grant delete on table "public"."documents" to "service_role";

grant insert on table "public"."documents" to "service_role";

grant references on table "public"."documents" to "service_role";

grant select on table "public"."documents" to "service_role";

grant trigger on table "public"."documents" to "service_role";

grant truncate on table "public"."documents" to "service_role";

grant update on table "public"."documents" to "service_role";

grant delete on table "public"."embeddings" to "anon";

grant insert on table "public"."embeddings" to "anon";

grant references on table "public"."embeddings" to "anon";

grant select on table "public"."embeddings" to "anon";

grant trigger on table "public"."embeddings" to "anon";

grant truncate on table "public"."embeddings" to "anon";

grant update on table "public"."embeddings" to "anon";

grant delete on table "public"."embeddings" to "authenticated";

grant insert on table "public"."embeddings" to "authenticated";

grant references on table "public"."embeddings" to "authenticated";

grant select on table "public"."embeddings" to "authenticated";

grant trigger on table "public"."embeddings" to "authenticated";

grant truncate on table "public"."embeddings" to "authenticated";

grant update on table "public"."embeddings" to "authenticated";

grant delete on table "public"."embeddings" to "service_role";

grant insert on table "public"."embeddings" to "service_role";

grant references on table "public"."embeddings" to "service_role";

grant select on table "public"."embeddings" to "service_role";

grant trigger on table "public"."embeddings" to "service_role";

grant truncate on table "public"."embeddings" to "service_role";

grant update on table "public"."embeddings" to "service_role";

grant delete on table "public"."messages" to "anon";

grant insert on table "public"."messages" to "anon";

grant references on table "public"."messages" to "anon";

grant select on table "public"."messages" to "anon";

grant trigger on table "public"."messages" to "anon";

grant truncate on table "public"."messages" to "anon";

grant update on table "public"."messages" to "anon";

grant delete on table "public"."messages" to "authenticated";

grant insert on table "public"."messages" to "authenticated";

grant references on table "public"."messages" to "authenticated";

grant select on table "public"."messages" to "authenticated";

grant trigger on table "public"."messages" to "authenticated";

grant truncate on table "public"."messages" to "authenticated";

grant update on table "public"."messages" to "authenticated";

grant delete on table "public"."messages" to "service_role";

grant insert on table "public"."messages" to "service_role";

grant references on table "public"."messages" to "service_role";

grant select on table "public"."messages" to "service_role";

grant trigger on table "public"."messages" to "service_role";

grant truncate on table "public"."messages" to "service_role";

grant update on table "public"."messages" to "service_role";

grant delete on table "public"."organizations" to "anon";

grant insert on table "public"."organizations" to "anon";

grant references on table "public"."organizations" to "anon";

grant select on table "public"."organizations" to "anon";

grant trigger on table "public"."organizations" to "anon";

grant truncate on table "public"."organizations" to "anon";

grant update on table "public"."organizations" to "anon";

grant delete on table "public"."organizations" to "authenticated";

grant insert on table "public"."organizations" to "authenticated";

grant references on table "public"."organizations" to "authenticated";

grant select on table "public"."organizations" to "authenticated";

grant trigger on table "public"."organizations" to "authenticated";

grant truncate on table "public"."organizations" to "authenticated";

grant update on table "public"."organizations" to "authenticated";

grant delete on table "public"."organizations" to "service_role";

grant insert on table "public"."organizations" to "service_role";

grant references on table "public"."organizations" to "service_role";

grant select on table "public"."organizations" to "service_role";

grant trigger on table "public"."organizations" to "service_role";

grant truncate on table "public"."organizations" to "service_role";

grant update on table "public"."organizations" to "service_role";

grant delete on table "public"."users" to "anon";

grant insert on table "public"."users" to "anon";

grant references on table "public"."users" to "anon";

grant select on table "public"."users" to "anon";

grant trigger on table "public"."users" to "anon";

grant truncate on table "public"."users" to "anon";

grant update on table "public"."users" to "anon";

grant delete on table "public"."users" to "authenticated";

grant insert on table "public"."users" to "authenticated";

grant references on table "public"."users" to "authenticated";

grant select on table "public"."users" to "authenticated";

grant trigger on table "public"."users" to "authenticated";

grant truncate on table "public"."users" to "authenticated";

grant update on table "public"."users" to "authenticated";

grant delete on table "public"."users" to "service_role";

grant insert on table "public"."users" to "service_role";

grant references on table "public"."users" to "service_role";

grant select on table "public"."users" to "service_role";

grant trigger on table "public"."users" to "service_role";

grant truncate on table "public"."users" to "service_role";

grant update on table "public"."users" to "service_role";


  create policy "chunks_select"
  on "public"."chunks"
  as permissive
  for select
  to public
using ((org_id = public.auth_org_id()));



  create policy "conversations_delete"
  on "public"."conversations"
  as permissive
  for delete
  to public
using (((org_id = public.auth_org_id()) AND (user_id = auth.uid())));



  create policy "conversations_insert"
  on "public"."conversations"
  as permissive
  for insert
  to public
with check (((org_id = public.auth_org_id()) AND (user_id = auth.uid())));



  create policy "conversations_select"
  on "public"."conversations"
  as permissive
  for select
  to public
using (((org_id = public.auth_org_id()) AND (user_id = auth.uid())));



  create policy "conversations_update"
  on "public"."conversations"
  as permissive
  for update
  to public
using (((org_id = public.auth_org_id()) AND (user_id = auth.uid())));



  create policy "documents_delete"
  on "public"."documents"
  as permissive
  for delete
  to public
using ((org_id = public.auth_org_id()));



  create policy "documents_insert"
  on "public"."documents"
  as permissive
  for insert
  to public
with check ((org_id = public.auth_org_id()));



  create policy "documents_select"
  on "public"."documents"
  as permissive
  for select
  to public
using ((org_id = public.auth_org_id()));



  create policy "documents_update"
  on "public"."documents"
  as permissive
  for update
  to public
using ((org_id = public.auth_org_id()));



  create policy "embeddings_select"
  on "public"."embeddings"
  as permissive
  for select
  to public
using ((org_id = public.auth_org_id()));



  create policy "messages_insert"
  on "public"."messages"
  as permissive
  for insert
  to public
with check ((org_id = public.auth_org_id()));



  create policy "messages_select"
  on "public"."messages"
  as permissive
  for select
  to public
using ((org_id = public.auth_org_id()));



  create policy "messages_update"
  on "public"."messages"
  as permissive
  for update
  to public
using ((org_id = public.auth_org_id()));



  create policy "org_select"
  on "public"."organizations"
  as permissive
  for select
  to public
using ((id = public.auth_org_id()));



  create policy "org_update"
  on "public"."organizations"
  as permissive
  for update
  to public
using ((id = public.auth_org_id()));



  create policy "users_insert"
  on "public"."users"
  as permissive
  for insert
  to public
with check (((org_id = public.auth_org_id()) AND (( SELECT users_1.role
   FROM public.users users_1
  WHERE (users_1.id = auth.uid())) = 'admin'::text)));



  create policy "users_select"
  on "public"."users"
  as permissive
  for select
  to public
using ((org_id = public.auth_org_id()));



  create policy "users_update_self"
  on "public"."users"
  as permissive
  for update
  to public
using ((id = auth.uid()));



  create policy "Users can delete their org files e91o2j_0"
  on "storage"."objects"
  as permissive
  for delete
  to authenticated
using (((bucket_id = 'document'::text) AND ((storage.foldername(name))[1] = 'orgs'::text) AND ((storage.foldername(name))[2] = ( SELECT (users.org_id)::text AS org_id
   FROM public.users
  WHERE (users.id = auth.uid())))));



  create policy "Users can read their org files e91o2j_0"
  on "storage"."objects"
  as permissive
  for select
  to authenticated
using (((bucket_id = 'document'::text) AND ((storage.foldername(name))[1] = 'orgs'::text) AND ((storage.foldername(name))[2] = ( SELECT (users.org_id)::text AS org_id
   FROM public.users
  WHERE (users.id = auth.uid())))));



  create policy "Users can upload to their org folder e91o2j_0"
  on "storage"."objects"
  as permissive
  for insert
  to authenticated
with check (((bucket_id = 'document'::text) AND ((storage.foldername(name))[1] = 'orgs'::text) AND ((storage.foldername(name))[2] = ( SELECT (users.org_id)::text AS org_id
   FROM public.users
  WHERE (users.id = auth.uid())))));




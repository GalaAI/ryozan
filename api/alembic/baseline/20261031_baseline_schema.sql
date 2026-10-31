CREATE TABLE public.bttco_ai_memories (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    agent character varying(20) NOT NULL,
    category character varying(30) NOT NULL,
    key character varying(100) NOT NULL,
    value text NOT NULL,
    confidence double precision NOT NULL,
    source character varying(30) NOT NULL,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    expires_at timestamp with time zone
);

CREATE TABLE public.bttco_api_usage (
    id uuid NOT NULL,
    user_id uuid,
    endpoint character varying(100) NOT NULL,
    input_tokens integer NOT NULL,
    output_tokens integer NOT NULL,
    estimated_cost double precision NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_artist_session_types (
    id uuid NOT NULL,
    artist_id uuid NOT NULL,
    label character varying(255) NOT NULL,
    description text,
    pricing_mode character varying(50) DEFAULT 'range'::character varying NOT NULL,
    size character varying(50) NOT NULL,
    placements text[],
    duration_minutes integer NOT NULL,
    buffer_minutes integer,
    deposit_amount numeric(10,2) NOT NULL,
    hourly_rate numeric(10,2),
    price_min numeric(10,2),
    price_max numeric(10,2),
    is_active boolean NOT NULL,
    sort_order integer NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_booking_addon_selections (
    id uuid NOT NULL,
    booking_id uuid NOT NULL,
    add_on_id uuid,
    label character varying(120) NOT NULL,
    description text,
    price_amount numeric(10,2),
    duration_minutes integer,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_booking_addons (
    id uuid NOT NULL,
    artist_id uuid NOT NULL,
    label character varying(120) NOT NULL,
    description text,
    price_amount numeric(10,2),
    duration_minutes integer,
    is_active boolean NOT NULL,
    sort_order integer NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_booking_drafts (
    id uuid NOT NULL,
    inquiry_id character varying(100) NOT NULL,
    status character varying(50) NOT NULL,
    artist_slug character varying(100),
    guest_name character varying(255),
    guest_email character varying(255),
    guest_phone character varying(50),
    form_data jsonb,
    image_urls jsonb,
    reference_code character varying(20),
    last_error text,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_booking_lifecycle_events (
    id uuid NOT NULL,
    booking_id uuid,
    artist_id uuid,
    actor_id uuid,
    event_type character varying(80) NOT NULL,
    source character varying(80) NOT NULL,
    provider_event_id character varying(255),
    reference_code character varying(40),
    hold_token character varying(128),
    guest_email character varying(255),
    summary text NOT NULL,
    details jsonb,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_booking_slot_holds (
    id uuid NOT NULL,
    hold_token character varying(64) NOT NULL,
    artist_id uuid NOT NULL,
    artist_slug character varying(100) NOT NULL,
    scheduled_date date NOT NULL,
    scheduled_time character varying(5) NOT NULL,
    duration_minutes integer NOT NULL,
    booking_kind character varying(50) NOT NULL,
    status character varying(24) NOT NULL,
    inquiry_id character varying(100),
    guest_email character varying(255),
    booking_id uuid,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_bookings (
    id uuid NOT NULL,
    client_id uuid,
    artist_id uuid NOT NULL,
    guest_name character varying(255),
    guest_email character varying(255),
    guest_phone character varying(50),
    sms_opt_in boolean NOT NULL,
    sms_opt_in_at timestamp with time zone,
    sms_consent_text text,
    reference_code character varying(20),
    status character varying(50) NOT NULL,
    description text NOT NULL,
    placement character varying(255),
    size_estimate character varying(100),
    style_preference character varying(255),
    reference_urls text,
    budget character varying(50),
    timeline character varying(50),
    preferred_date date,
    preferred_time character varying(50),
    preferred_slots_json jsonb,
    scheduled_date date,
    scheduled_time character varying(50),
    duration_minutes integer,
    deposit_amount numeric(10,2),
    estimated_total numeric(10,2),
    deposit_paid boolean NOT NULL,
    is_bring_your_own_flash boolean DEFAULT false NOT NULL,
    deposit_authorization_status character varying(40),
    deposit_authorized_at timestamp with time zone,
    deposit_review_deadline_at timestamp with time zone,
    deposit_review_reminder_sent_at timestamp with time zone,
    artist_notes text,
    client_notes text,
    stripe_checkout_session_id character varying(255),
    stripe_payment_intent_id character varying(255),
    google_calendar_event_id character varying(255),
    source character varying(50) NOT NULL,
    booking_kind character varying(50) DEFAULT 'tattoo_session'::character varying NOT NULL,
    consultation_method character varying(50),
    booky_consent boolean DEFAULT false NOT NULL,
    booky_consent_at timestamp with time zone,
    booky_consent_policy_version character varying(64),
    booky_collector_verified_at timestamp with time zone,
    booky_capability_inquiry_id character varying(100),
    conversation_id uuid,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_broadcasts (
    id uuid NOT NULL,
    author_id uuid NOT NULL,
    title character varying(255) NOT NULL,
    body text NOT NULL,
    broadcast_type character varying(50) NOT NULL,
    pinned boolean NOT NULL,
    audience character varying(50) NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_club_post_likes (
    id uuid NOT NULL,
    post_id uuid NOT NULL,
    user_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_club_posts (
    id uuid NOT NULL,
    author_id uuid NOT NULL,
    body text NOT NULL,
    media_url character varying(500),
    post_type character varying(50) NOT NULL,
    likes_count integer NOT NULL,
    is_pinned boolean NOT NULL,
    is_deleted boolean NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_conversation_participants (
    id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    user_id uuid NOT NULL,
    role character varying(50) NOT NULL,
    joined_at timestamp with time zone NOT NULL,
    last_read_at timestamp with time zone
);

CREATE TABLE public.bttco_conversations (
    id uuid NOT NULL,
    name character varying(255),
    is_group boolean NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_crm_pipeline_tasks (
    id uuid NOT NULL,
    title character varying(255) NOT NULL,
    body text,
    status character varying(32) NOT NULL,
    priority character varying(16),
    booking_id uuid,
    assignee_id uuid,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_document_instances (
    id uuid NOT NULL,
    template_id uuid,
    user_id uuid,
    guest_name character varying(255),
    guest_email character varying(255),
    guest_social character varying(255),
    category character varying(50) NOT NULL,
    title character varying(255) NOT NULL,
    status character varying(50) NOT NULL,
    data_json text,
    signed_at timestamp with time zone,
    file_url text,
    notes text,
    send_token character varying(64),
    recipient_email character varying(255),
    recipient_name character varying(255),
    sent_at timestamp with time zone,
    sent_by uuid,
    viewed_at timestamp with time zone,
    reminder_at timestamp with time zone,
    reminder_sent boolean NOT NULL,
    expires_at timestamp with time zone,
    artist_signature text,
    artist_signed_at timestamp with time zone,
    artist_id_reviewed uuid,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_document_templates (
    id uuid NOT NULL,
    name character varying(255) NOT NULL,
    category character varying(50) NOT NULL,
    description text,
    fields_json text NOT NULL,
    is_active boolean NOT NULL,
    requires_signature boolean NOT NULL,
    created_by uuid,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_econ_connections (
    id uuid NOT NULL,
    provider character varying(32) NOT NULL,
    owner_user_id uuid,
    display_name character varying(120),
    status character varying(24) NOT NULL,
    environment character varying(32),
    external_account_id character varying(255),
    auth_secret_ref character varying(255),
    sync_cursor text,
    last_synced_at timestamp with time zone,
    metadata_json jsonb,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_econ_ledger_entries (
    id uuid NOT NULL,
    provider character varying(32) NOT NULL,
    connection_id uuid,
    external_id character varying(255) NOT NULL,
    entry_type character varying(32) NOT NULL,
    status character varying(32) NOT NULL,
    amount_cents integer NOT NULL,
    currency character varying(8) NOT NULL,
    occurred_at timestamp with time zone,
    description text,
    counterparty character varying(255),
    category character varying(120),
    payroll_tax_bucket character varying(64),
    artist_id uuid,
    raw jsonb,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_econ_sync_runs (
    id uuid NOT NULL,
    provider character varying(32) NOT NULL,
    connection_id uuid,
    status character varying(24) NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    added_count integer NOT NULL,
    modified_count integer NOT NULL,
    removed_count integer NOT NULL,
    error text,
    metadata_json jsonb
);

CREATE TABLE public.bttco_email_log (
    id uuid NOT NULL,
    to_email character varying(320) NOT NULL,
    subject character varying(500) NOT NULL,
    template character varying(100) NOT NULL,
    from_alias character varying(50),
    status character varying(20) NOT NULL,
    resend_id character varying(100),
    error text,
    courier_message_id uuid,
    courier_attempt integer,
    retry_count integer NOT NULL,
    last_retry_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_email_routes (
    id uuid NOT NULL,
    handle character varying(100) NOT NULL,
    forward_to character varying(255) NOT NULL,
    user_id uuid NOT NULL,
    verified boolean NOT NULL,
    verify_token character varying(255),
    created_at timestamp with time zone NOT NULL,
    verified_at timestamp with time zone
);

CREATE TABLE public.bttco_email_templates (
    id uuid NOT NULL,
    slug character varying(100) NOT NULL,
    name character varying(255) NOT NULL,
    subject character varying(500) NOT NULL,
    category character varying(50) NOT NULL,
    html_body text NOT NULL,
    variables jsonb NOT NULL,
    is_active boolean NOT NULL,
    description text,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_flash_designs (
    id uuid NOT NULL,
    sheet_id uuid NOT NULL,
    artist_id uuid NOT NULL,
    crop_x numeric(8,4) NOT NULL,
    crop_y numeric(8,4) NOT NULL,
    crop_w numeric(8,4) NOT NULL,
    crop_h numeric(8,4) NOT NULL,
    image_url text,
    name character varying(255),
    ai_suggested_name character varying(255),
    price numeric(10,2),
    estimated_minutes integer,
    size_label character varying(100),
    placement character varying(120),
    style character varying(100),
    mask_json jsonb,
    canvas_state jsonb,
    ai_metadata jsonb,
    gala_sync_enabled boolean NOT NULL,
    status character varying(50) NOT NULL,
    claimed_by uuid,
    claimed_at timestamp with time zone,
    booking_id uuid,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_flash_sheets (
    id uuid NOT NULL,
    artist_id uuid NOT NULL,
    image_url text NOT NULL,
    source_image_url text,
    crop_applied boolean DEFAULT false NOT NULL,
    crop_x numeric(8,4) DEFAULT '0'::numeric NOT NULL,
    crop_y numeric(8,4) DEFAULT '0'::numeric NOT NULL,
    crop_w numeric(8,4) DEFAULT '100'::numeric NOT NULL,
    crop_h numeric(8,4) DEFAULT '100'::numeric NOT NULL,
    asset_version integer DEFAULT 1 NOT NULL,
    title character varying(255),
    status character varying(50) NOT NULL,
    design_count integer NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_gala_feedback (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    message text NOT NULL,
    status character varying(30) NOT NULL,
    priority integer NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_gallery_deletion_outbox (
    id uuid NOT NULL,
    artist_id uuid NOT NULL,
    idempotency_key character varying(64) NOT NULL,
    url text NOT NULL,
    allowed_prefix character varying(300) NOT NULL,
    status character varying(20) NOT NULL,
    attempts integer NOT NULL,
    last_error text,
    next_attempt_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_gift_card_redemptions (
    id uuid NOT NULL,
    gift_card_id uuid NOT NULL,
    amount_cents integer NOT NULL,
    booking_id uuid,
    note text,
    redeemed_by uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_gift_card_redemption_amount_positive CHECK ((amount_cents > 0))
);

CREATE TABLE public.bttco_gift_cards (
    id uuid NOT NULL,
    code character varying(32) NOT NULL,
    artist_id uuid NOT NULL,
    purchaser_name character varying(255) NOT NULL,
    purchaser_email character varying(320) NOT NULL,
    recipient_name character varying(255) NOT NULL,
    recipient_email character varying(320),
    send_to_recipient boolean DEFAULT true NOT NULL,
    message text,
    original_amount_cents integer NOT NULL,
    balance_cents integer NOT NULL,
    currency character varying(3) DEFAULT 'USD'::character varying NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    stripe_session_id character varying(255),
    stripe_payment_intent_id character varying(255),
    routing_destination character varying(20) NOT NULL,
    routing_reason character varying(40) NOT NULL,
    artist_cut_cents integer NOT NULL,
    shop_cut_cents integer NOT NULL,
    payout_owed_cents integer NOT NULL,
    delivered_at timestamp with time zone,
    activated_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_gift_card_balance_nonnegative CHECK ((balance_cents >= 0)),
    CONSTRAINT ck_gift_card_balance_within_original CHECK ((balance_cents <= original_amount_cents)),
    CONSTRAINT ck_gift_card_original_amount_positive CHECK ((original_amount_cents > 0)),
    CONSTRAINT ck_gift_card_status CHECK (status IN ('pending', 'active', 'redeemed', 'voided'))
);

CREATE TABLE public.bttco_mcp_oauth_authorization_codes (
    code_hash character varying(64) NOT NULL,
    client_id character varying(160) NOT NULL,
    user_id uuid NOT NULL,
    redirect_uri text NOT NULL,
    scope text NOT NULL,
    resource text NOT NULL,
    code_challenge character varying(128) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_mcp_oauth_clients (
    id uuid NOT NULL,
    client_id character varying(160) NOT NULL,
    client_name character varying(255) NOT NULL,
    redirect_uris jsonb NOT NULL,
    grant_types jsonb NOT NULL,
    response_types jsonb NOT NULL,
    token_endpoint_auth_method character varying(40) NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_mcp_oauth_refresh_tokens (
    token_hash character varying(64) NOT NULL,
    client_id character varying(160) NOT NULL,
    user_id uuid NOT NULL,
    scope text NOT NULL,
    resource text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_messages (
    id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    sender_id uuid NOT NULL,
    content text NOT NULL,
    message_type character varying(50) NOT NULL,
    metadata_json text,
    artifact_payload jsonb,
    artifact_state jsonb,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone
);

CREATE TABLE public.bttco_notification_preferences (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    email_bookings boolean NOT NULL,
    email_messages boolean NOT NULL,
    email_quests boolean NOT NULL,
    email_marketing boolean NOT NULL,
    push_bookings boolean NOT NULL,
    push_messages boolean NOT NULL,
    push_quests boolean NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_notifications (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    title character varying(255) NOT NULL,
    body text NOT NULL,
    notification_type character varying(50) NOT NULL,
    read boolean NOT NULL,
    action_url text,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_pack_bridge_outbox (
    id uuid NOT NULL,
    source_id character varying(160) NOT NULL,
    idempotency_key character varying(256) NOT NULL,
    payload jsonb NOT NULL,
    courier_message_id uuid,
    status character varying(20) NOT NULL,
    attempts integer NOT NULL,
    destination text,
    status_code integer,
    item_id character varying(255),
    created boolean,
    last_error text,
    next_attempt_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_pos_transactions (
    id uuid NOT NULL,
    artist_id uuid NOT NULL,
    booking_id uuid,
    amount_cents integer NOT NULL,
    commission_base_cents integer DEFAULT 0 NOT NULL,
    price_adjustment_cents integer DEFAULT 0 NOT NULL,
    payment_method character varying(20) NOT NULL,
    processor character varying(50),
    client_name character varying(255),
    description text,
    stripe_session_id character varying(255),
    attempt_version integer,
    request_key character varying(128),
    request_fingerprint character varying(64),
    collection_method character varying(12),
    attempt_state character varying(32),
    attempt_revision integer,
    currency character varying(3),
    livemode boolean,
    tip_cents integer,
    stripe_payment_intent_id character varying(255),
    stripe_account_id character varying(255),
    routing_destination character varying(20),
    routing_reason character varying(64),
    commission_rate integer,
    artist_cut_cents integer,
    artist_tip_cents integer,
    shop_cut_cents integer,
    payout_owed_cents integer,
    balance_snapshot jsonb,
    receipt_context jsonb,
    terminal_reader_id character varying(255),
    reader_account_id character varying(255),
    reader_ownership character varying(24),
    reader_eligibility_ref character varying(255),
    checkout_url text,
    updated_at timestamp with time zone,
    settled_at timestamp with time zone,
    status character varying(20) NOT NULL,
    notes text,
    created_at timestamp with time zone NOT NULL,
    created_by uuid NOT NULL,
    CONSTRAINT ck_pos_attempt_v1_amounts CHECK (((attempt_version IS NULL) OR ((amount_cents > 0) AND (commission_base_cents >= 0) AND (price_adjustment_cents >= 0) AND (tip_cents >= 0) AND ((amount_cents)::bigint = (((commission_base_cents)::bigint + price_adjustment_cents) + tip_cents)) AND ((commission_rate >= 0) AND (commission_rate <= 100)) AND (artist_cut_cents >= 0) AND (shop_cut_cents >= 0) AND ((artist_tip_cents = 0) OR (artist_tip_cents = tip_cents)) AND ((artist_cut_cents)::bigint = ((((commission_base_cents)::bigint * commission_rate) / 100) + artist_tip_cents)) AND (payout_owed_cents >= 0) AND ((amount_cents)::bigint = ((artist_cut_cents)::bigint + shop_cut_cents))))),
    CONSTRAINT ck_pos_attempt_v1_collection CHECK (attempt_version IS NULL OR (payment_method = 'card' AND processor IS NOT NULL AND processor = 'stripe' AND ((collection_method = 'qr' AND terminal_reader_id IS NULL AND reader_account_id IS NULL AND reader_ownership IS NULL AND reader_eligibility_ref IS NULL) OR (collection_method = 'reader' AND terminal_reader_id IS NOT NULL AND terminal_reader_id ~ '^tmr_[A-Za-z0-9]+$' AND reader_ownership IS NOT NULL AND ((reader_ownership = 'connected_account' AND routing_destination = 'artist' AND reader_account_id IS NOT NULL AND reader_account_id = stripe_account_id AND reader_eligibility_ref IS NULL) OR (reader_ownership IN ('platform', 'platform_preview') AND reader_account_id IS NULL AND ((reader_ownership = 'platform' AND routing_destination = 'shop' AND reader_eligibility_ref IS NULL) OR (reader_ownership = 'platform_preview' AND routing_destination = 'artist' AND reader_eligibility_ref IS NOT NULL AND length(trim(reader_eligibility_ref)) > 0)))))))),
    CONSTRAINT ck_pos_attempt_v1_context CHECK (((attempt_version IS NULL) OR ((jsonb_typeof(balance_snapshot) = 'object'::text) AND (balance_snapshot @> '{"version": 1, "currency": "USD"}'::jsonb) AND (balance_snapshot ?& ARRAY['service_total_cents'::text, 'paid_service_cents'::text, 'refunded_service_cents'::text, 'remaining_service_cents'::text, 'evidence_keys'::text, 'livemode'::text]) AND ((balance_snapshot -> 'livemode'::text) = to_jsonb(livemode)) AND (jsonb_typeof((balance_snapshot -> 'evidence_keys'::text)) = 'array'::text) AND (jsonb_typeof((balance_snapshot -> 'service_total_cents'::text)) = 'number'::text) AND (jsonb_typeof((balance_snapshot -> 'paid_service_cents'::text)) = 'number'::text) AND (jsonb_typeof((balance_snapshot -> 'refunded_service_cents'::text)) = 'number'::text) AND (jsonb_typeof((balance_snapshot -> 'remaining_service_cents'::text)) = 'number'::text) AND (((balance_snapshot ->> 'service_total_cents'::text))::bigint >= 0) AND (((balance_snapshot ->> 'paid_service_cents'::text))::bigint >= 0) AND ((((balance_snapshot ->> 'refunded_service_cents'::text))::bigint >= 0) AND (((balance_snapshot ->> 'refunded_service_cents'::text))::bigint <= ((balance_snapshot ->> 'paid_service_cents'::text))::bigint)) AND (((balance_snapshot ->> 'remaining_service_cents'::text))::bigint = GREATEST((0)::bigint, ((((balance_snapshot ->> 'service_total_cents'::text))::bigint - ((balance_snapshot ->> 'paid_service_cents'::text))::bigint) + ((balance_snapshot ->> 'refunded_service_cents'::text))::bigint))) AND (commission_base_cents <= ((balance_snapshot ->> 'remaining_service_cents'::text))::bigint) AND (jsonb_typeof(receipt_context) = 'object'::text) AND (receipt_context ? 'description'::text) AND (jsonb_typeof((receipt_context -> 'description'::text)) = 'string'::text) AND (length(TRIM(BOTH FROM (receipt_context ->> 'description'::text))) > 0) AND (receipt_context @> '{"version": 1, "refund_policy": "all_payments_final", "processing_fee_policy": "included_in_displayed_amount"}'::jsonb)))),
    CONSTRAINT ck_pos_attempt_v1_presence CHECK ((((attempt_version IS NULL) AND (request_key IS NULL) AND (request_fingerprint IS NULL) AND (collection_method IS NULL) AND (attempt_state IS NULL) AND (attempt_revision IS NULL) AND (currency IS NULL) AND (livemode IS NULL) AND (tip_cents IS NULL) AND (stripe_payment_intent_id IS NULL) AND (stripe_account_id IS NULL) AND (routing_destination IS NULL) AND (routing_reason IS NULL) AND (commission_rate IS NULL) AND (artist_cut_cents IS NULL) AND (artist_tip_cents IS NULL) AND (shop_cut_cents IS NULL) AND (payout_owed_cents IS NULL) AND (balance_snapshot IS NULL) AND (receipt_context IS NULL) AND (terminal_reader_id IS NULL) AND (reader_account_id IS NULL) AND (reader_ownership IS NULL) AND (reader_eligibility_ref IS NULL) AND (checkout_url IS NULL) AND (updated_at IS NULL) AND (settled_at IS NULL)) OR ((attempt_version IS NOT NULL) AND (attempt_version = 1) AND (request_key IS NOT NULL) AND ((request_key)::text ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$'::text) AND (request_fingerprint IS NOT NULL) AND ((request_fingerprint)::text ~ '^[0-9a-f]{64}$'::text) AND (collection_method IS NOT NULL) AND (attempt_state IS NOT NULL) AND (attempt_revision IS NOT NULL) AND (attempt_revision >= 0) AND (currency IS NOT NULL) AND ((currency)::text = 'USD'::text) AND (livemode IS NOT NULL) AND (tip_cents IS NOT NULL) AND (routing_destination IS NOT NULL) AND (routing_reason IS NOT NULL) AND (length((routing_reason)::text) > 0) AND (commission_rate IS NOT NULL) AND (artist_cut_cents IS NOT NULL) AND (artist_tip_cents IS NOT NULL) AND (shop_cut_cents IS NOT NULL) AND (payout_owed_cents IS NOT NULL) AND (balance_snapshot IS NOT NULL) AND (receipt_context IS NOT NULL) AND (updated_at IS NOT NULL)))),
    CONSTRAINT ck_pos_attempt_v1_provider_refs CHECK (((attempt_version IS NULL) OR (((stripe_payment_intent_id IS NULL) OR ((stripe_payment_intent_id)::text ~ '^pi_[A-Za-z0-9]+$'::text)) AND ((stripe_session_id IS NULL) OR ((stripe_session_id)::text ~ '^cs_[A-Za-z0-9_]+$'::text)) AND (((collection_method)::text = 'qr'::text) OR ((stripe_session_id IS NULL) AND (checkout_url IS NULL)))))),
    CONSTRAINT ck_pos_attempt_v1_routing CHECK (((attempt_version IS NULL) OR ((((routing_destination)::text = 'artist'::text) AND (stripe_account_id IS NOT NULL) AND ((stripe_account_id)::text ~ '^acct_[A-Za-z0-9]+$'::text) AND (payout_owed_cents = 0)) OR (((routing_destination)::text = 'shop'::text) AND (stripe_account_id IS NULL) AND (payout_owed_cents = artist_cut_cents))))),
    CONSTRAINT ck_pos_attempt_v1_state CHECK (attempt_version IS NULL OR ((attempt_state IN ('prepared', 'awaiting_payment', 'processing', 'cancel_requested', 'reconciliation_required') AND status = 'pending' AND settled_at IS NULL) OR (attempt_state = 'succeeded' AND status = 'completed' AND settled_at IS NOT NULL AND stripe_payment_intent_id IS NOT NULL) OR (attempt_state IN ('canceled', 'failed') AND status = 'voided' AND settled_at IS NULL)))
);

CREATE TABLE public.bttco_push_subscriptions (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    endpoint text NOT NULL,
    p256dh text NOT NULL,
    auth text NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_quests (
    id uuid NOT NULL,
    slug character varying(80) NOT NULL,
    title character varying(255) NOT NULL,
    description text NOT NULL,
    category character varying(50) NOT NULL,
    xp_reward integer NOT NULL,
    coin_reward integer NOT NULL,
    requirement character varying(255) NOT NULL,
    target_count integer NOT NULL,
    is_active boolean NOT NULL,
    sort_order integer NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_quotes (
    id uuid NOT NULL,
    booking_id uuid NOT NULL,
    token character varying(64) NOT NULL,
    estimated_total_min numeric(10,2),
    estimated_total_max numeric(10,2),
    deposit_amount numeric(10,2) NOT NULL,
    available_slots_json text,
    artist_message text,
    status character varying(50) NOT NULL,
    sent_at timestamp with time zone NOT NULL,
    viewed_at timestamp with time zone,
    accepted_at timestamp with time zone,
    expires_at timestamp with time zone,
    selected_date timestamp with time zone,
    selected_time character varying(50),
    selected_duration integer,
    stripe_checkout_session_id character varying(255),
    stripe_payment_intent_id character varying(255),
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_reference_views (
    id uuid NOT NULL,
    reference_id uuid NOT NULL,
    user_id uuid NOT NULL,
    viewed_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_references (
    id uuid NOT NULL,
    filename character varying(500) NOT NULL,
    original_filename character varying(500) NOT NULL,
    mime_type character varying(100) NOT NULL,
    file_size integer NOT NULL,
    file_url text NOT NULL,
    uploaded_by uuid NOT NULL,
    is_pinned boolean NOT NULL,
    pin_order integer,
    tags jsonb NOT NULL,
    view_count integer NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_response_templates (
    id uuid NOT NULL,
    title character varying(120) NOT NULL,
    body text NOT NULL,
    sort_order integer NOT NULL,
    created_by uuid,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_reward_grants (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    event_type character varying(80) NOT NULL,
    source_id character varying(255) NOT NULL,
    idempotency_key character varying(340) NOT NULL,
    xp_delta integer NOT NULL,
    coin_delta integer NOT NULL,
    description text NOT NULL,
    unlock_kind character varying(40),
    unlock_slug character varying(120),
    metadata_json jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_reward_offers (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    trigger_grant_id uuid NOT NULL,
    offer_type character varying(40) NOT NULL,
    game_slug character varying(80) NOT NULL,
    reward_kind character varying(40) NOT NULL,
    reward_slug character varying(120) NOT NULL,
    status character varying(24) NOT NULL,
    expires_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_scroll_categories (
    id uuid NOT NULL,
    slug character varying(80) NOT NULL,
    label character varying(120) NOT NULL,
    icon character varying(60),
    description text,
    is_builtin boolean NOT NULL,
    sort_order integer NOT NULL,
    ai_digest_enabled boolean NOT NULL,
    ai_digest_prompt text,
    created_by uuid,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_scroll_owner_email_outbox (
    id uuid NOT NULL,
    founder_id uuid,
    submission_id character varying(64) NOT NULL,
    to_email character varying(320) NOT NULL,
    subject character varying(500) NOT NULL,
    html_body text NOT NULL,
    attachment_filename character varying(255),
    attachment_content bytea,
    status character varying(20) NOT NULL,
    attempts integer NOT NULL,
    last_error text,
    next_attempt_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_sms_messages (
    id uuid NOT NULL,
    direction character varying(10) NOT NULL,
    to_phone character varying(20) NOT NULL,
    from_phone character varying(20) NOT NULL,
    body text NOT NULL,
    status character varying(30) NOT NULL,
    error_code character varying(40),
    error_message text,
    twilio_sid character varying(64),
    template_slug character varying(100),
    conversation_id uuid,
    courier_message_id uuid,
    user_id uuid,
    sender_id uuid,
    price_cents integer,
    num_segments integer,
    created_at timestamp with time zone NOT NULL,
    delivered_at timestamp with time zone
);

CREATE TABLE public.bttco_sms_templates (
    id uuid NOT NULL,
    slug character varying(100) NOT NULL,
    name character varying(255) NOT NULL,
    category character varying(50) NOT NULL,
    body text NOT NULL,
    variables jsonb NOT NULL,
    is_active boolean NOT NULL,
    description text,
    owner_id uuid,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_stash_canvas_sessions (
    id uuid NOT NULL,
    artist_id uuid NOT NULL,
    sheet_id uuid,
    created_by_id uuid,
    lane character varying(32) NOT NULL,
    status character varying(32) NOT NULL,
    canvas_state jsonb,
    ai_suggestions jsonb,
    source_context jsonb,
    gala_sync_enabled boolean NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_stash_items (
    id uuid NOT NULL,
    artist_id uuid NOT NULL,
    created_by_id uuid,
    source_flash_design_id uuid,
    lane character varying(32) NOT NULL,
    title character varying(255) NOT NULL,
    description text,
    status character varying(32) NOT NULL,
    sku character varying(120),
    asset_url text,
    price numeric(10,2),
    inventory_count integer,
    size_label character varying(100),
    estimated_minutes integer,
    delivery_kind character varying(50),
    storage_ref text,
    tags jsonb,
    metadata jsonb,
    ai_metadata jsonb,
    gala_sync_enabled boolean NOT NULL,
    published_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_time_blocks (
    id uuid NOT NULL,
    artist_id uuid NOT NULL,
    block_date date NOT NULL,
    start_time character varying(5),
    end_time character varying(5),
    all_day boolean NOT NULL,
    reason character varying(255),
    google_calendar_event_id character varying(255),
    created_by uuid,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_user_identity_aliases (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    email character varying(255) NOT NULL,
    google_id character varying(255),
    password_hash character varying(255),
    password_binding character varying(32) NOT NULL,
    source_snapshot jsonb NOT NULL,
    reference_snapshot jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_user_quests (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    quest_id uuid NOT NULL,
    progress integer NOT NULL,
    completed boolean NOT NULL,
    claimed boolean NOT NULL,
    completed_at timestamp with time zone,
    claimed_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_users (
    id uuid NOT NULL,
    email character varying(255) NOT NULL,
    password_hash character varying(255) NOT NULL,
    token_version integer DEFAULT 0 NOT NULL,
    name character varying(255) NOT NULL,
    role character varying(50) NOT NULL,
    avatar_url text,
    bttcoin_balance integer NOT NULL,
    quest_points integer NOT NULL,
    marketing_opt_in boolean NOT NULL,
    must_reset_password boolean NOT NULL,
    email_verified_at timestamp with time zone,
    password_state character varying(24),
    password_changed_at timestamp with time zone,
    password_change_method character varying(16),
    last_login_at timestamp with time zone,
    last_login_method character varying(16),
    reasons character varying[],
    membership_tier character varying(50) NOT NULL,
    membership_status character varying(50) NOT NULL,
    stripe_customer_id character varying(255),
    booth_rent_amount_cents integer,
    booth_rent_interval character varying(16),
    booth_rent_payment_method character varying(16) DEFAULT 'cash'::character varying NOT NULL,
    booth_rent_status character varying(24) DEFAULT 'not_configured'::character varying NOT NULL,
    booth_rent_subscription_id character varying(255),
    booth_rent_checkout_session_id character varying(255),
    booth_rent_current_period_end timestamp with time zone,
    booth_rent_next_due_date date,
    booth_rent_cancel_at_period_end boolean DEFAULT false NOT NULL,
    ai_monthly_message_cap integer NOT NULL,
    early_access_galax3 boolean NOT NULL,
    connected_apps_unlocked boolean NOT NULL,
    product_entitlements jsonb NOT NULL,
    slug character varying(100),
    title character varying(255),
    specialty character varying[],
    bio text,
    booking_mode character varying(50),
    booking_url character varying(500),
    instagram_url character varying(500),
    portfolio_images character varying[],
    portfolio_thumbs jsonb,
    portfolio_focus jsonb,
    stripe_account_id character varying(255),
    stripe_onboarding_complete boolean NOT NULL,
    commission_rate integer,
    is_guest_artist boolean DEFAULT false NOT NULL,
    plaid_access_token character varying(512),
    availability_source character varying(50) NOT NULL,
    booking_slot_interval_minutes integer NOT NULL,
    booking_buffer_minutes integer NOT NULL,
    booking_buffer_enabled boolean DEFAULT true NOT NULL,
    booking_min_notice_hours integer NOT NULL,
    booking_max_advance_days integer NOT NULL,
    booking_allow_same_day boolean NOT NULL,
    booky_mode character varying(24) DEFAULT 'manual'::character varying NOT NULL,
    booky_opted_in_at timestamp with time zone,
    google_refresh_token character varying(512),
    google_calendar_id character varying(255),
    google_id character varying(255),
    pinterest_refresh_token character varying(1024),
    pinterest_access_token character varying(1024),
    pinterest_username character varying(255),
    gala_personality integer NOT NULL,
    gala_auto_reply character varying(50),
    nexus_profile jsonb,
    saved_signature_url character varying(500),
    flash_deposit_amount numeric(8,2),
    allow_prepay_full boolean NOT NULL,
    proxy_for uuid,
    phone character varying(50),
    sms_opt_in boolean NOT NULL,
    sms_opt_in_at timestamp with time zone,
    sms_opt_out_at timestamp with time zone,
    facebook_url character varying(500),
    tiktok_url character varying(500),
    website_url character varying(500),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_workspace_channels (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    discord_channel_id character varying(32) NOT NULL,
    discord_guild_id character varying(32),
    bound_by character varying(80),
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_workspace_feature_status (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    domain character varying(120) NOT NULL,
    live_rows integer NOT NULL,
    db_tables jsonb,
    api character varying(255),
    ui character varying(255),
    status character varying(16) NOT NULL,
    verdict character varying(16),
    notes text,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_workspace_log (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    author character varying(80) NOT NULL,
    entry text NOT NULL,
    source character varying(24) NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_workspace_notes (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    author character varying(80) NOT NULL,
    body text NOT NULL,
    source character varying(24) NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.bttco_workspace_todos (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    title character varying(280) NOT NULL,
    status character varying(16) NOT NULL,
    assignee character varying(80),
    author character varying(80) NOT NULL,
    source character varying(24) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    done_at timestamp with time zone
);

CREATE TABLE public.bttco_workspaces (
    id uuid NOT NULL,
    slug character varying(64) NOT NULL,
    name character varying(120) NOT NULL,
    kind character varying(32) NOT NULL,
    description text,
    archived boolean NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.feedback_samples (
    id uuid NOT NULL,
    model_id character varying(100) NOT NULL,
    model_version character varying(255) NOT NULL,
    input_type character varying(50) NOT NULL,
    input_ref text NOT NULL,
    artifact_ref text,
    subject_id uuid,
    prediction jsonb NOT NULL,
    correction jsonb,
    accepted boolean NOT NULL,
    outcome character varying(24) NOT NULL,
    source character varying(50) NOT NULL,
    module character varying(50) NOT NULL,
    training_status character varying(24) NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.viber_accounts (
    id uuid NOT NULL,
    email character varying(255) NOT NULL,
    name character varying(120) NOT NULL,
    plan character varying(24) NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.viber_ai_providers (
    id uuid NOT NULL,
    account_id uuid NOT NULL,
    provider_type character varying(16) NOT NULL,
    label character varying(80) NOT NULL,
    base_url character varying(512),
    default_model character varying(120),
    key_secret_ref character varying(255),
    is_default boolean NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.viber_connections (
    id uuid NOT NULL,
    account_id uuid NOT NULL,
    name character varying(120) NOT NULL,
    kind character varying(32) NOT NULL,
    base_url character varying(512) NOT NULL,
    capability character varying(16) NOT NULL,
    auth_scheme character varying(32) NOT NULL,
    auth_secret_ref character varying(255),
    status character varying(24) NOT NULL,
    last_checked_at timestamp with time zone,
    introspection jsonb,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE public.viber_proposals (
    id uuid NOT NULL,
    account_id uuid NOT NULL,
    connection_id uuid NOT NULL,
    agent character varying(32) NOT NULL,
    intent text NOT NULL,
    status character varying(24) NOT NULL,
    proposal jsonb NOT NULL,
    approval_notes text,
    created_at timestamp with time zone NOT NULL,
    approved_at timestamp with time zone,
    executed_at timestamp with time zone
);

ALTER TABLE ONLY public.bttco_ai_memories
    ADD CONSTRAINT bttco_ai_memories_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_api_usage
    ADD CONSTRAINT bttco_api_usage_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_artist_session_types
    ADD CONSTRAINT bttco_artist_session_types_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_booking_addon_selections
    ADD CONSTRAINT bttco_booking_addon_selections_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_booking_addons
    ADD CONSTRAINT bttco_booking_addons_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_booking_drafts
    ADD CONSTRAINT bttco_booking_drafts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_booking_lifecycle_events
    ADD CONSTRAINT bttco_booking_lifecycle_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_booking_slot_holds
    ADD CONSTRAINT bttco_booking_slot_holds_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_bookings
    ADD CONSTRAINT bttco_bookings_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_broadcasts
    ADD CONSTRAINT bttco_broadcasts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_club_post_likes
    ADD CONSTRAINT bttco_club_post_likes_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_club_posts
    ADD CONSTRAINT bttco_club_posts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_conversation_participants
    ADD CONSTRAINT bttco_conversation_participants_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_conversations
    ADD CONSTRAINT bttco_conversations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_crm_pipeline_tasks
    ADD CONSTRAINT bttco_crm_pipeline_tasks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_document_instances
    ADD CONSTRAINT bttco_document_instances_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_document_templates
    ADD CONSTRAINT bttco_document_templates_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_econ_connections
    ADD CONSTRAINT bttco_econ_connections_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_econ_ledger_entries
    ADD CONSTRAINT bttco_econ_ledger_entries_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_econ_sync_runs
    ADD CONSTRAINT bttco_econ_sync_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_email_log
    ADD CONSTRAINT bttco_email_log_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_email_log
    ADD CONSTRAINT bttco_email_log_resend_id_key UNIQUE (resend_id);

ALTER TABLE ONLY public.bttco_email_routes
    ADD CONSTRAINT bttco_email_routes_handle_key UNIQUE (handle);

ALTER TABLE ONLY public.bttco_email_routes
    ADD CONSTRAINT bttco_email_routes_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_email_templates
    ADD CONSTRAINT bttco_email_templates_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_email_templates
    ADD CONSTRAINT bttco_email_templates_slug_key UNIQUE (slug);

ALTER TABLE ONLY public.bttco_flash_designs
    ADD CONSTRAINT bttco_flash_designs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_flash_sheets
    ADD CONSTRAINT bttco_flash_sheets_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_gala_feedback
    ADD CONSTRAINT bttco_gala_feedback_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_gallery_deletion_outbox
    ADD CONSTRAINT bttco_gallery_deletion_outbox_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_gift_card_redemptions
    ADD CONSTRAINT bttco_gift_card_redemptions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_gift_cards
    ADD CONSTRAINT bttco_gift_cards_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_gift_cards
    ADD CONSTRAINT bttco_gift_cards_stripe_session_id_key UNIQUE (stripe_session_id);

ALTER TABLE ONLY public.bttco_mcp_oauth_authorization_codes
    ADD CONSTRAINT bttco_mcp_oauth_authorization_codes_pkey PRIMARY KEY (code_hash);

ALTER TABLE ONLY public.bttco_mcp_oauth_clients
    ADD CONSTRAINT bttco_mcp_oauth_clients_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_mcp_oauth_refresh_tokens
    ADD CONSTRAINT bttco_mcp_oauth_refresh_tokens_pkey PRIMARY KEY (token_hash);

ALTER TABLE ONLY public.bttco_messages
    ADD CONSTRAINT bttco_messages_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_notification_preferences
    ADD CONSTRAINT bttco_notification_preferences_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_notification_preferences
    ADD CONSTRAINT bttco_notification_preferences_user_id_key UNIQUE (user_id);

ALTER TABLE ONLY public.bttco_notifications
    ADD CONSTRAINT bttco_notifications_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_pack_bridge_outbox
    ADD CONSTRAINT bttco_pack_bridge_outbox_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_pos_transactions
    ADD CONSTRAINT bttco_pos_transactions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_push_subscriptions
    ADD CONSTRAINT bttco_push_subscriptions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_quests
    ADD CONSTRAINT bttco_quests_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_quests
    ADD CONSTRAINT bttco_quests_slug_key UNIQUE (slug);

ALTER TABLE ONLY public.bttco_quotes
    ADD CONSTRAINT bttco_quotes_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_reference_views
    ADD CONSTRAINT bttco_reference_views_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_references
    ADD CONSTRAINT bttco_references_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_response_templates
    ADD CONSTRAINT bttco_response_templates_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_reward_grants
    ADD CONSTRAINT bttco_reward_grants_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_reward_offers
    ADD CONSTRAINT bttco_reward_offers_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_reward_offers
    ADD CONSTRAINT bttco_reward_offers_trigger_grant_id_key UNIQUE (trigger_grant_id);

ALTER TABLE ONLY public.bttco_scroll_categories
    ADD CONSTRAINT bttco_scroll_categories_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_scroll_categories
    ADD CONSTRAINT bttco_scroll_categories_slug_key UNIQUE (slug);

ALTER TABLE ONLY public.bttco_scroll_owner_email_outbox
    ADD CONSTRAINT bttco_scroll_owner_email_outbox_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_sms_messages
    ADD CONSTRAINT bttco_sms_messages_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_sms_messages
    ADD CONSTRAINT bttco_sms_messages_twilio_sid_key UNIQUE (twilio_sid);

ALTER TABLE ONLY public.bttco_sms_templates
    ADD CONSTRAINT bttco_sms_templates_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_sms_templates
    ADD CONSTRAINT bttco_sms_templates_slug_key UNIQUE (slug);

ALTER TABLE ONLY public.bttco_stash_canvas_sessions
    ADD CONSTRAINT bttco_stash_canvas_sessions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_stash_items
    ADD CONSTRAINT bttco_stash_items_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_time_blocks
    ADD CONSTRAINT bttco_time_blocks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_user_identity_aliases
    ADD CONSTRAINT bttco_user_identity_aliases_email_key UNIQUE (email);

ALTER TABLE ONLY public.bttco_user_identity_aliases
    ADD CONSTRAINT bttco_user_identity_aliases_google_id_key UNIQUE (google_id);

ALTER TABLE ONLY public.bttco_user_identity_aliases
    ADD CONSTRAINT bttco_user_identity_aliases_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_user_quests
    ADD CONSTRAINT bttco_user_quests_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_users
    ADD CONSTRAINT bttco_users_email_key UNIQUE (email);

ALTER TABLE ONLY public.bttco_users
    ADD CONSTRAINT bttco_users_google_id_key UNIQUE (google_id);

ALTER TABLE ONLY public.bttco_users
    ADD CONSTRAINT bttco_users_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_users
    ADD CONSTRAINT bttco_users_slug_key UNIQUE (slug);

ALTER TABLE ONLY public.bttco_workspace_channels
    ADD CONSTRAINT bttco_workspace_channels_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_workspace_feature_status
    ADD CONSTRAINT bttco_workspace_feature_status_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_workspace_log
    ADD CONSTRAINT bttco_workspace_log_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_workspace_notes
    ADD CONSTRAINT bttco_workspace_notes_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_workspace_todos
    ADD CONSTRAINT bttco_workspace_todos_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_workspaces
    ADD CONSTRAINT bttco_workspaces_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.feedback_samples
    ADD CONSTRAINT feedback_samples_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bttco_econ_connections
    ADD CONSTRAINT uq_econ_connection_provider_owner UNIQUE (provider, owner_user_id);

ALTER TABLE ONLY public.bttco_econ_ledger_entries
    ADD CONSTRAINT uq_econ_ledger_provider_external_id UNIQUE (provider, external_id);

ALTER TABLE ONLY public.bttco_ai_memories
    ADD CONSTRAINT uq_memory_user_agent_cat_key UNIQUE (user_id, agent, category, key);

ALTER TABLE ONLY public.bttco_push_subscriptions
    ADD CONSTRAINT uq_push_user_endpoint UNIQUE (user_id, endpoint);

ALTER TABLE ONLY public.bttco_reward_grants
    ADD CONSTRAINT uq_reward_grant_user_key UNIQUE (user_id, idempotency_key);

ALTER TABLE ONLY public.bttco_user_quests
    ADD CONSTRAINT uq_user_quest UNIQUE (user_id, quest_id);

ALTER TABLE ONLY public.viber_accounts
    ADD CONSTRAINT viber_accounts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.viber_ai_providers
    ADD CONSTRAINT viber_ai_providers_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.viber_connections
    ADD CONSTRAINT viber_connections_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.viber_proposals
    ADD CONSTRAINT viber_proposals_pkey PRIMARY KEY (id);

CREATE INDEX idx_bttco_api_usage_created_at ON public.bttco_api_usage USING btree (created_at);

CREATE INDEX idx_bttco_bookings_artist_id ON public.bttco_bookings USING btree (artist_id);

CREATE INDEX idx_bttco_bookings_artist_status ON public.bttco_bookings USING btree (artist_id, status);

CREATE INDEX idx_bttco_bookings_client_id ON public.bttco_bookings USING btree (client_id);

CREATE INDEX idx_bttco_bookings_conversation_id ON public.bttco_bookings USING btree (conversation_id) WHERE (conversation_id IS NOT NULL);

CREATE INDEX idx_bttco_bookings_created_at ON public.bttco_bookings USING btree (created_at);

CREATE INDEX idx_bttco_bookings_reference_code ON public.bttco_bookings USING btree (reference_code) WHERE (reference_code IS NOT NULL);

CREATE INDEX idx_bttco_bookings_scheduled_date ON public.bttco_bookings USING btree (scheduled_date) WHERE (scheduled_date IS NOT NULL);

CREATE INDEX idx_bttco_bookings_status ON public.bttco_bookings USING btree (status);

CREATE INDEX idx_bttco_notifications_read ON public.bttco_notifications USING btree (user_id, read) WHERE (read = false);

CREATE INDEX idx_bttco_notifications_user_id ON public.bttco_notifications USING btree (user_id);

CREATE UNIQUE INDEX idx_bttco_quests_slug_unique ON public.bttco_quests USING btree (slug);

CREATE INDEX idx_bttco_users_role ON public.bttco_users USING btree (role);

CREATE INDEX ix_bttco_api_usage_endpoint ON public.bttco_api_usage USING btree (endpoint);

CREATE INDEX ix_bttco_api_usage_user_id ON public.bttco_api_usage USING btree (user_id);

CREATE INDEX ix_bttco_booking_addon_selections_booking_id ON public.bttco_booking_addon_selections USING btree (booking_id);

CREATE INDEX ix_bttco_booking_addons_artist_id ON public.bttco_booking_addons USING btree (artist_id);

CREATE INDEX ix_bttco_booking_drafts_guest_email ON public.bttco_booking_drafts USING btree (guest_email);

CREATE UNIQUE INDEX ix_bttco_booking_drafts_inquiry_id ON public.bttco_booking_drafts USING btree (inquiry_id);

CREATE INDEX ix_bttco_booking_drafts_reference_code ON public.bttco_booking_drafts USING btree (reference_code);

CREATE INDEX ix_bttco_booking_lifecycle_events_artist_id ON public.bttco_booking_lifecycle_events USING btree (artist_id);

CREATE INDEX ix_bttco_booking_lifecycle_events_booking_id ON public.bttco_booking_lifecycle_events USING btree (booking_id);

CREATE INDEX ix_bttco_booking_lifecycle_events_created_at ON public.bttco_booking_lifecycle_events USING btree (created_at);

CREATE INDEX ix_bttco_booking_lifecycle_events_event_type ON public.bttco_booking_lifecycle_events USING btree (event_type);

CREATE INDEX ix_bttco_booking_lifecycle_events_guest_email ON public.bttco_booking_lifecycle_events USING btree (guest_email);

CREATE INDEX ix_bttco_booking_lifecycle_events_hold_token ON public.bttco_booking_lifecycle_events USING btree (hold_token);

CREATE UNIQUE INDEX ix_bttco_booking_lifecycle_events_provider_event_id ON public.bttco_booking_lifecycle_events USING btree (provider_event_id);

CREATE INDEX ix_bttco_booking_lifecycle_events_reference_code ON public.bttco_booking_lifecycle_events USING btree (reference_code);

CREATE INDEX ix_bttco_booking_slot_holds_artist_slug ON public.bttco_booking_slot_holds USING btree (artist_slug);

CREATE INDEX ix_bttco_booking_slot_holds_expires_at ON public.bttco_booking_slot_holds USING btree (expires_at);

CREATE INDEX ix_bttco_booking_slot_holds_guest_email ON public.bttco_booking_slot_holds USING btree (guest_email);

CREATE UNIQUE INDEX ix_bttco_booking_slot_holds_hold_token ON public.bttco_booking_slot_holds USING btree (hold_token);

CREATE INDEX ix_bttco_booking_slot_holds_inquiry_id ON public.bttco_booking_slot_holds USING btree (inquiry_id);

CREATE INDEX ix_bttco_booking_slot_holds_scheduled_date ON public.bttco_booking_slot_holds USING btree (scheduled_date);

CREATE INDEX ix_bttco_booking_slot_holds_status ON public.bttco_booking_slot_holds USING btree (status);

CREATE INDEX ix_bttco_bookings_deposit_review_deadline_at ON public.bttco_bookings USING btree (deposit_review_deadline_at);

CREATE UNIQUE INDEX ix_bttco_bookings_reference_code ON public.bttco_bookings USING btree (reference_code);

CREATE INDEX ix_bttco_crm_pipeline_tasks_booking_id ON public.bttco_crm_pipeline_tasks USING btree (booking_id);

CREATE UNIQUE INDEX ix_bttco_document_instances_send_token ON public.bttco_document_instances USING btree (send_token);

CREATE INDEX ix_bttco_econ_connections_owner_user_id ON public.bttco_econ_connections USING btree (owner_user_id);

CREATE INDEX ix_bttco_econ_connections_provider ON public.bttco_econ_connections USING btree (provider);

CREATE INDEX ix_bttco_econ_ledger_entries_artist_id ON public.bttco_econ_ledger_entries USING btree (artist_id);

CREATE INDEX ix_bttco_econ_ledger_entries_connection_id ON public.bttco_econ_ledger_entries USING btree (connection_id);

CREATE INDEX ix_bttco_econ_ledger_entries_occurred_at ON public.bttco_econ_ledger_entries USING btree (occurred_at);

CREATE INDEX ix_bttco_econ_ledger_entries_provider ON public.bttco_econ_ledger_entries USING btree (provider);

CREATE INDEX ix_bttco_econ_sync_runs_connection_id ON public.bttco_econ_sync_runs USING btree (connection_id);

CREATE INDEX ix_bttco_econ_sync_runs_provider ON public.bttco_econ_sync_runs USING btree (provider);

CREATE INDEX ix_bttco_email_log_courier_message_attempt ON public.bttco_email_log USING btree (courier_message_id, courier_attempt);

CREATE INDEX ix_bttco_email_log_status ON public.bttco_email_log USING btree (status);

CREATE INDEX ix_bttco_email_log_template ON public.bttco_email_log USING btree (template);

CREATE INDEX ix_bttco_email_log_to_email ON public.bttco_email_log USING btree (to_email);

CREATE INDEX ix_bttco_gallery_deletion_outbox_artist_id ON public.bttco_gallery_deletion_outbox USING btree (artist_id);

CREATE UNIQUE INDEX ix_bttco_gallery_deletion_outbox_idempotency_key ON public.bttco_gallery_deletion_outbox USING btree (idempotency_key);

CREATE INDEX ix_bttco_gallery_deletion_outbox_next_attempt_at ON public.bttco_gallery_deletion_outbox USING btree (next_attempt_at);

CREATE INDEX ix_bttco_gallery_deletion_outbox_status ON public.bttco_gallery_deletion_outbox USING btree (status);

CREATE INDEX ix_bttco_gift_card_redemptions_gift_card_id ON public.bttco_gift_card_redemptions USING btree (gift_card_id);

CREATE INDEX ix_bttco_gift_cards_artist_id ON public.bttco_gift_cards USING btree (artist_id);

CREATE UNIQUE INDEX ix_bttco_gift_cards_code ON public.bttco_gift_cards USING btree (code);

CREATE INDEX ix_bttco_mcp_oauth_authorization_codes_client_id ON public.bttco_mcp_oauth_authorization_codes USING btree (client_id);

CREATE INDEX ix_bttco_mcp_oauth_authorization_codes_expires_at ON public.bttco_mcp_oauth_authorization_codes USING btree (expires_at);

CREATE INDEX ix_bttco_mcp_oauth_authorization_codes_user_id ON public.bttco_mcp_oauth_authorization_codes USING btree (user_id);

CREATE UNIQUE INDEX ix_bttco_mcp_oauth_clients_client_id ON public.bttco_mcp_oauth_clients USING btree (client_id);

CREATE INDEX ix_bttco_mcp_oauth_refresh_tokens_client_id ON public.bttco_mcp_oauth_refresh_tokens USING btree (client_id);

CREATE INDEX ix_bttco_mcp_oauth_refresh_tokens_expires_at ON public.bttco_mcp_oauth_refresh_tokens USING btree (expires_at);

CREATE INDEX ix_bttco_mcp_oauth_refresh_tokens_user_id ON public.bttco_mcp_oauth_refresh_tokens USING btree (user_id);

CREATE INDEX ix_bttco_pack_bridge_outbox_courier_message_id ON public.bttco_pack_bridge_outbox USING btree (courier_message_id);

CREATE UNIQUE INDEX ix_bttco_pack_bridge_outbox_idempotency_key ON public.bttco_pack_bridge_outbox USING btree (idempotency_key);

CREATE INDEX ix_bttco_pack_bridge_outbox_next_attempt_at ON public.bttco_pack_bridge_outbox USING btree (next_attempt_at);

CREATE INDEX ix_bttco_pack_bridge_outbox_source_id ON public.bttco_pack_bridge_outbox USING btree (source_id);

CREATE INDEX ix_bttco_pack_bridge_outbox_status ON public.bttco_pack_bridge_outbox USING btree (status);

CREATE UNIQUE INDEX ix_bttco_quotes_token ON public.bttco_quotes USING btree (token);

CREATE INDEX ix_bttco_reward_grants_created_at ON public.bttco_reward_grants USING btree (created_at);

CREATE INDEX ix_bttco_reward_grants_event_type ON public.bttco_reward_grants USING btree (event_type);

CREATE INDEX ix_bttco_reward_grants_unlock_kind ON public.bttco_reward_grants USING btree (unlock_kind);

CREATE INDEX ix_bttco_reward_grants_user_id ON public.bttco_reward_grants USING btree (user_id);

CREATE INDEX ix_bttco_reward_offers_created_at ON public.bttco_reward_offers USING btree (created_at);

CREATE INDEX ix_bttco_reward_offers_status ON public.bttco_reward_offers USING btree (status);

CREATE INDEX ix_bttco_reward_offers_user_id ON public.bttco_reward_offers USING btree (user_id);

CREATE INDEX ix_bttco_scroll_owner_email_outbox_next_attempt_at ON public.bttco_scroll_owner_email_outbox USING btree (next_attempt_at);

CREATE INDEX ix_bttco_scroll_owner_email_outbox_status ON public.bttco_scroll_owner_email_outbox USING btree (status);

CREATE INDEX ix_bttco_scroll_owner_email_outbox_submission_id ON public.bttco_scroll_owner_email_outbox USING btree (submission_id);

CREATE INDEX ix_bttco_sms_messages_courier_message_id ON public.bttco_sms_messages USING btree (courier_message_id);

CREATE INDEX ix_bttco_stash_canvas_artist_lane ON public.bttco_stash_canvas_sessions USING btree (artist_id, lane);

CREATE INDEX ix_bttco_stash_canvas_sheet_id ON public.bttco_stash_canvas_sessions USING btree (sheet_id);

CREATE INDEX ix_bttco_stash_items_artist_lane ON public.bttco_stash_items USING btree (artist_id, lane);

CREATE INDEX ix_bttco_stash_items_status ON public.bttco_stash_items USING btree (status);

CREATE INDEX ix_bttco_time_blocks_artist_date ON public.bttco_time_blocks USING btree (artist_id, block_date);

CREATE INDEX ix_bttco_time_blocks_artist_id ON public.bttco_time_blocks USING btree (artist_id);

CREATE INDEX ix_bttco_time_blocks_block_date ON public.bttco_time_blocks USING btree (block_date);

CREATE INDEX ix_bttco_user_identity_aliases_user_id ON public.bttco_user_identity_aliases USING btree (user_id);

CREATE UNIQUE INDEX ix_bttco_users_google_id ON public.bttco_users USING btree (google_id) WHERE (google_id IS NOT NULL);

CREATE INDEX ix_bttco_users_membership_tier ON public.bttco_users USING btree (membership_tier);

CREATE UNIQUE INDEX ix_bttco_workspace_channels_discord_channel_id ON public.bttco_workspace_channels USING btree (discord_channel_id);

CREATE INDEX ix_bttco_workspace_channels_workspace_id ON public.bttco_workspace_channels USING btree (workspace_id);

CREATE INDEX ix_bttco_workspace_feature_status_workspace_id ON public.bttco_workspace_feature_status USING btree (workspace_id);

CREATE INDEX ix_bttco_workspace_log_workspace_id ON public.bttco_workspace_log USING btree (workspace_id);

CREATE INDEX ix_bttco_workspace_notes_workspace_id ON public.bttco_workspace_notes USING btree (workspace_id);

CREATE INDEX ix_bttco_workspace_todos_workspace_id ON public.bttco_workspace_todos USING btree (workspace_id);

CREATE UNIQUE INDEX ix_bttco_workspaces_slug ON public.bttco_workspaces USING btree (slug);

CREATE INDEX ix_feedback_samples_model_created ON public.feedback_samples USING btree (model_id, created_at);

CREATE INDEX ix_feedback_samples_subject_created ON public.feedback_samples USING btree (subject_id, created_at);

CREATE INDEX ix_sms_messages_created_at ON public.bttco_sms_messages USING btree (created_at);

CREATE INDEX ix_sms_messages_direction ON public.bttco_sms_messages USING btree (direction);

CREATE INDEX ix_sms_messages_status ON public.bttco_sms_messages USING btree (status);

CREATE INDEX ix_sms_messages_to_phone ON public.bttco_sms_messages USING btree (to_phone);

CREATE INDEX ix_sms_templates_owner_id ON public.bttco_sms_templates USING btree (owner_id);

CREATE UNIQUE INDEX ix_viber_accounts_email ON public.viber_accounts USING btree (email);

CREATE INDEX ix_viber_ai_providers_account_id ON public.viber_ai_providers USING btree (account_id);

CREATE INDEX ix_viber_connections_account_id ON public.viber_connections USING btree (account_id);

CREATE INDEX ix_viber_proposals_account_id ON public.viber_proposals USING btree (account_id);

CREATE INDEX ix_viber_proposals_connection_id ON public.viber_proposals USING btree (connection_id);

CREATE UNIQUE INDEX ux_bttco_pos_transactions_stripe_session_id ON public.bttco_pos_transactions USING btree (stripe_session_id) WHERE (stripe_session_id IS NOT NULL);

CREATE UNIQUE INDEX ux_bttco_scroll_categories_slug ON public.bttco_scroll_categories USING btree (slug);

CREATE UNIQUE INDEX ux_bttco_users_booth_rent_subscription_id ON public.bttco_users USING btree (booth_rent_subscription_id) WHERE (booth_rent_subscription_id IS NOT NULL);

CREATE UNIQUE INDEX ux_bttco_users_email_lower ON public.bttco_users USING btree (lower((email)::text));

CREATE UNIQUE INDEX ux_pos_attempt_v1_active_booking ON bttco_pos_transactions (booking_id) WHERE attempt_version = 1 AND booking_id IS NOT NULL AND attempt_state IN ('prepared', 'awaiting_payment', 'processing', 'cancel_requested', 'reconciliation_required');

CREATE UNIQUE INDEX ux_pos_attempt_v1_active_reader ON bttco_pos_transactions (coalesce(reader_account_id, ''), terminal_reader_id) WHERE attempt_version = 1 AND terminal_reader_id IS NOT NULL AND attempt_state IN ('prepared', 'awaiting_payment', 'processing', 'cancel_requested', 'reconciliation_required');

CREATE UNIQUE INDEX ux_pos_attempt_v1_payment_intent ON public.bttco_pos_transactions USING btree (livemode, COALESCE(stripe_account_id, ''::character varying), stripe_payment_intent_id) WHERE ((attempt_version = 1) AND (stripe_payment_intent_id IS NOT NULL));

CREATE UNIQUE INDEX ux_pos_attempt_v1_request ON public.bttco_pos_transactions USING btree (created_by, request_key) WHERE (attempt_version = 1);

ALTER TABLE ONLY public.bttco_ai_memories
    ADD CONSTRAINT bttco_ai_memories_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_artist_session_types
    ADD CONSTRAINT bttco_artist_session_types_artist_id_fkey FOREIGN KEY (artist_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_booking_addon_selections
    ADD CONSTRAINT bttco_booking_addon_selections_add_on_id_fkey FOREIGN KEY (add_on_id) REFERENCES public.bttco_booking_addons(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.bttco_booking_addon_selections
    ADD CONSTRAINT bttco_booking_addon_selections_booking_id_fkey FOREIGN KEY (booking_id) REFERENCES public.bttco_bookings(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_booking_addons
    ADD CONSTRAINT bttco_booking_addons_artist_id_fkey FOREIGN KEY (artist_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_booking_lifecycle_events
    ADD CONSTRAINT bttco_booking_lifecycle_events_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.bttco_users(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.bttco_booking_lifecycle_events
    ADD CONSTRAINT bttco_booking_lifecycle_events_artist_id_fkey FOREIGN KEY (artist_id) REFERENCES public.bttco_users(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.bttco_booking_lifecycle_events
    ADD CONSTRAINT bttco_booking_lifecycle_events_booking_id_fkey FOREIGN KEY (booking_id) REFERENCES public.bttco_bookings(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_booking_slot_holds
    ADD CONSTRAINT bttco_booking_slot_holds_artist_id_fkey FOREIGN KEY (artist_id) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_booking_slot_holds
    ADD CONSTRAINT bttco_booking_slot_holds_booking_id_fkey FOREIGN KEY (booking_id) REFERENCES public.bttco_bookings(id);

ALTER TABLE ONLY public.bttco_bookings
    ADD CONSTRAINT bttco_bookings_artist_id_fkey FOREIGN KEY (artist_id) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_bookings
    ADD CONSTRAINT bttco_bookings_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_bookings
    ADD CONSTRAINT bttco_bookings_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.bttco_conversations(id);

ALTER TABLE ONLY public.bttco_broadcasts
    ADD CONSTRAINT bttco_broadcasts_author_id_fkey FOREIGN KEY (author_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_club_post_likes
    ADD CONSTRAINT bttco_club_post_likes_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.bttco_club_posts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_club_post_likes
    ADD CONSTRAINT bttco_club_post_likes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_club_posts
    ADD CONSTRAINT bttco_club_posts_author_id_fkey FOREIGN KEY (author_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_conversation_participants
    ADD CONSTRAINT bttco_conversation_participants_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.bttco_conversations(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_conversation_participants
    ADD CONSTRAINT bttco_conversation_participants_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_conversations
    ADD CONSTRAINT bttco_conversations_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_document_instances
    ADD CONSTRAINT bttco_document_instances_artist_id_reviewed_fkey FOREIGN KEY (artist_id_reviewed) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_document_instances
    ADD CONSTRAINT bttco_document_instances_sent_by_fkey FOREIGN KEY (sent_by) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_document_instances
    ADD CONSTRAINT bttco_document_instances_template_id_fkey FOREIGN KEY (template_id) REFERENCES public.bttco_document_templates(id);

ALTER TABLE ONLY public.bttco_document_instances
    ADD CONSTRAINT bttco_document_instances_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_document_templates
    ADD CONSTRAINT bttco_document_templates_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_email_routes
    ADD CONSTRAINT bttco_email_routes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_flash_designs
    ADD CONSTRAINT bttco_flash_designs_artist_id_fkey FOREIGN KEY (artist_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_flash_designs
    ADD CONSTRAINT bttco_flash_designs_booking_id_fkey FOREIGN KEY (booking_id) REFERENCES public.bttco_bookings(id);

ALTER TABLE ONLY public.bttco_flash_designs
    ADD CONSTRAINT bttco_flash_designs_claimed_by_fkey FOREIGN KEY (claimed_by) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_flash_designs
    ADD CONSTRAINT bttco_flash_designs_sheet_id_fkey FOREIGN KEY (sheet_id) REFERENCES public.bttco_flash_sheets(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_flash_sheets
    ADD CONSTRAINT bttco_flash_sheets_artist_id_fkey FOREIGN KEY (artist_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_gala_feedback
    ADD CONSTRAINT bttco_gala_feedback_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_gallery_deletion_outbox
    ADD CONSTRAINT bttco_gallery_deletion_outbox_artist_id_fkey FOREIGN KEY (artist_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_gift_card_redemptions
    ADD CONSTRAINT bttco_gift_card_redemptions_booking_id_fkey FOREIGN KEY (booking_id) REFERENCES public.bttco_bookings(id);

ALTER TABLE ONLY public.bttco_gift_card_redemptions
    ADD CONSTRAINT bttco_gift_card_redemptions_gift_card_id_fkey FOREIGN KEY (gift_card_id) REFERENCES public.bttco_gift_cards(id);

ALTER TABLE ONLY public.bttco_gift_card_redemptions
    ADD CONSTRAINT bttco_gift_card_redemptions_redeemed_by_fkey FOREIGN KEY (redeemed_by) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_gift_cards
    ADD CONSTRAINT bttco_gift_cards_artist_id_fkey FOREIGN KEY (artist_id) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_messages
    ADD CONSTRAINT bttco_messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.bttco_conversations(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_messages
    ADD CONSTRAINT bttco_messages_sender_id_fkey FOREIGN KEY (sender_id) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_notification_preferences
    ADD CONSTRAINT bttco_notification_preferences_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_notifications
    ADD CONSTRAINT bttco_notifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_pack_bridge_outbox
    ADD CONSTRAINT bttco_pack_bridge_outbox_courier_message_id_fkey FOREIGN KEY (courier_message_id) REFERENCES public.bttco_messages(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.bttco_pos_transactions
    ADD CONSTRAINT bttco_pos_transactions_artist_id_fkey FOREIGN KEY (artist_id) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_pos_transactions
    ADD CONSTRAINT bttco_pos_transactions_booking_id_fkey FOREIGN KEY (booking_id) REFERENCES public.bttco_bookings(id);

ALTER TABLE ONLY public.bttco_pos_transactions
    ADD CONSTRAINT bttco_pos_transactions_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_push_subscriptions
    ADD CONSTRAINT bttco_push_subscriptions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_quotes
    ADD CONSTRAINT bttco_quotes_booking_id_fkey FOREIGN KEY (booking_id) REFERENCES public.bttco_bookings(id);

ALTER TABLE ONLY public.bttco_reference_views
    ADD CONSTRAINT bttco_reference_views_reference_id_fkey FOREIGN KEY (reference_id) REFERENCES public.bttco_references(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_reference_views
    ADD CONSTRAINT bttco_reference_views_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_references
    ADD CONSTRAINT bttco_references_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_response_templates
    ADD CONSTRAINT bttco_response_templates_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.bttco_users(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.bttco_reward_grants
    ADD CONSTRAINT bttco_reward_grants_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_reward_offers
    ADD CONSTRAINT bttco_reward_offers_trigger_grant_id_fkey FOREIGN KEY (trigger_grant_id) REFERENCES public.bttco_reward_grants(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_reward_offers
    ADD CONSTRAINT bttco_reward_offers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_scroll_categories
    ADD CONSTRAINT bttco_scroll_categories_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_scroll_owner_email_outbox
    ADD CONSTRAINT bttco_scroll_owner_email_outbox_founder_id_fkey FOREIGN KEY (founder_id) REFERENCES public.bttco_users(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.bttco_sms_messages
    ADD CONSTRAINT bttco_sms_messages_courier_message_id_fkey FOREIGN KEY (courier_message_id) REFERENCES public.bttco_messages(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.bttco_stash_canvas_sessions
    ADD CONSTRAINT bttco_stash_canvas_sessions_artist_id_fkey FOREIGN KEY (artist_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_stash_canvas_sessions
    ADD CONSTRAINT bttco_stash_canvas_sessions_created_by_id_fkey FOREIGN KEY (created_by_id) REFERENCES public.bttco_users(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.bttco_stash_canvas_sessions
    ADD CONSTRAINT bttco_stash_canvas_sessions_sheet_id_fkey FOREIGN KEY (sheet_id) REFERENCES public.bttco_flash_sheets(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_stash_items
    ADD CONSTRAINT bttco_stash_items_artist_id_fkey FOREIGN KEY (artist_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_stash_items
    ADD CONSTRAINT bttco_stash_items_created_by_id_fkey FOREIGN KEY (created_by_id) REFERENCES public.bttco_users(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.bttco_stash_items
    ADD CONSTRAINT bttco_stash_items_source_flash_design_id_fkey FOREIGN KEY (source_flash_design_id) REFERENCES public.bttco_flash_designs(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.bttco_time_blocks
    ADD CONSTRAINT bttco_time_blocks_artist_id_fkey FOREIGN KEY (artist_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_time_blocks
    ADD CONSTRAINT bttco_time_blocks_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.bttco_users(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.bttco_user_identity_aliases
    ADD CONSTRAINT bttco_user_identity_aliases_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.bttco_users(id);

ALTER TABLE ONLY public.bttco_user_quests
    ADD CONSTRAINT bttco_user_quests_quest_id_fkey FOREIGN KEY (quest_id) REFERENCES public.bttco_quests(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bttco_user_quests
    ADD CONSTRAINT bttco_user_quests_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.bttco_users(id) ON DELETE CASCADE;

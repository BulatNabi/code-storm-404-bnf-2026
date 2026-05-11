-- ============================================================
--  RegTech Document Pipeline — PostgreSQL Schema
-- ============================================================

-- ──────────────────────────────────────────
--  SHARED / PUBLIC SCHEMA
-- ──────────────────────────────────────────

-- Document categories for fintech compliance
CREATE TABLE IF NOT EXISTS public.doc_categories (
    category_id   VARCHAR(64) PRIMARY KEY,
    name_ru       TEXT NOT NULL,
    name_en       TEXT NOT NULL,
    priority      INTEGER DEFAULT 2   -- 1=critical, 2=high, 3=medium
);

INSERT INTO public.doc_categories VALUES
  ('aml_cft',            'AML/CFT и финмониторинг',          'AML/CFT',                1),
  ('payments',           'Платежи, переводы, электронные деньги', 'Payments & Transfers', 1),
  ('kyc',                'KYC, идентификация, онбординг',     'KYC & Identification',   1),
  ('data_protection',    'Персональные данные и приватность', 'Data Protection',        1),
  ('licensing',          'Лицензирование и периметр',         'Licensing',              1),
  ('cybersecurity',      'Информационная безопасность',       'Cybersecurity',          1),
  ('consumer_protection','Защита прав потребителей',          'Consumer Protection',    2),
  ('crypto',             'Криптоактивы и цифровые активы',    'Crypto & Digital Assets',2),
  ('ai_scoring',         'ИИ, скоринг, автоматические решения','AI & Scoring',          2),
  ('reporting',          'Отчетность, аудит, хранение данных','Reporting & Audit',      2),
  ('international',      'Международные стандарты',           'International Standards',3)
ON CONFLICT DO NOTHING;


-- Source registry (one row per worker/site)
CREATE TABLE IF NOT EXISTS public.sources (
    source_id           VARCHAR(64) PRIMARY KEY,
    name                TEXT        NOT NULL,
    base_url            TEXT        NOT NULL,
    kafka_topic         VARCHAR(128)NOT NULL,
    schedule_hours      FLOAT       NOT NULL DEFAULT 12,
    last_checked_at     TIMESTAMPTZ,
    is_active           BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO public.sources (source_id, name, base_url, kafka_topic, schedule_hours) VALUES
  ('cbu',    'Центральный банк Узбекистана',  'https://cbu.uz/ru/documents/',   'reg.cbu',    12),
  ('lex',    'Lex.uz — Законодательство РУ',  'https://lex.uz/ru/',             'reg.lex',    24),
  ('eurlex', 'EUR-Lex — EU Regulations',      'https://eur-lex.europa.eu/',     'reg.eurlex', 168),
  ('fatf',   'FATF — AML/CFT Standards',      'https://www.fatf-gafi.org/',     'reg.fatf',   168)
ON CONFLICT DO NOTHING;


-- Central documents registry
CREATE TABLE IF NOT EXISTS public.documents (
    doc_id              VARCHAR(256) PRIMARY KEY,
    source_id           VARCHAR(64)  NOT NULL REFERENCES public.sources(source_id),
    name                TEXT         NOT NULL,
    source_url          TEXT         NOT NULL,
    s3_key              TEXT,
    file_hash           VARCHAR(128),
    file_size           BIGINT,
    category            VARCHAR(64)  REFERENCES public.doc_categories(category_id),
    language            VARCHAR(8)   NOT NULL DEFAULT 'ru',
    processing_status   VARCHAR(32)  NOT NULL DEFAULT 'pending',
    -- pending | processing | done | error
    first_seen_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    rules_extracted_at  TIMESTAMPTZ,
    rules_count         INTEGER      DEFAULT 0,
    error_message       TEXT
);

CREATE INDEX IF NOT EXISTS idx_documents_source    ON public.documents(source_id);
CREATE INDEX IF NOT EXISTS idx_documents_category  ON public.documents(category);
CREATE INDEX IF NOT EXISTS idx_documents_status    ON public.documents(processing_status);


-- ──────────────────────────────────────────
--  CBU SCHEMA — Central Bank of Uzbekistan
-- ──────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS cbu;

-- CBU normative acts raw metadata
CREATE TABLE IF NOT EXISTS cbu.normative_acts (
    id              SERIAL       PRIMARY KEY,
    doc_id          VARCHAR(256) REFERENCES public.documents(doc_id),
    cbu_category    VARCHAR(16)  NOT NULL,  -- 3311, 3312, 3313, 3314, 3315, 3316
    cbu_doc_number  VARCHAR(128),           -- e.g. "№ 1234" or internal number
    title_ru        TEXT,
    issued_date     DATE,
    effective_date  DATE,
    is_active       BOOLEAN      DEFAULT TRUE,
    detail_url      TEXT,
    scraped_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cbu_acts_category ON cbu.normative_acts(cbu_category);
CREATE INDEX IF NOT EXISTS idx_cbu_acts_doc_id   ON cbu.normative_acts(doc_id);


-- ──────────────────────────────────────────
--  LEX SCHEMA — Lex.uz national legislation
-- ──────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS lex;

CREATE TABLE IF NOT EXISTS lex.acts (
    id              SERIAL       PRIMARY KEY,
    doc_id          VARCHAR(256) REFERENCES public.documents(doc_id),
    lex_id          VARCHAR(64)  UNIQUE,    -- lex.uz internal ID
    act_number      VARCHAR(64),            -- e.g. "ЗРУ-547"
    act_type        VARCHAR(64),            -- Закон, Указ Президента, Постановление
    title_ru        TEXT,
    adopted_date    DATE,
    effective_date  DATE,
    is_active       BOOLEAN      DEFAULT TRUE,
    detail_url      TEXT,
    scraped_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lex_acts_number ON lex.acts(act_number);
CREATE INDEX IF NOT EXISTS idx_lex_acts_doc_id ON lex.acts(doc_id);


-- ──────────────────────────────────────────
--  EURLEX SCHEMA — EU Regulations
-- ──────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS eurlex;

CREATE TABLE IF NOT EXISTS eurlex.regulations (
    id              SERIAL       PRIMARY KEY,
    doc_id          VARCHAR(256) REFERENCES public.documents(doc_id),
    celex_number    VARCHAR(32)  UNIQUE,    -- e.g. "32016R0679"
    regulation_code VARCHAR(32)  NOT NULL,  -- GDPR, PSD2, AI_ACT, MICA, DORA
    regulation_name TEXT,
    official_ref    VARCHAR(128),           -- e.g. "EU 2016/679"
    version_date    DATE,
    in_force        BOOLEAN      DEFAULT TRUE,
    pdf_url         TEXT,
    checked_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_eurlex_code ON eurlex.regulations(regulation_code);


-- ──────────────────────────────────────────
--  FATF SCHEMA
-- ──────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS fatf;

CREATE TABLE IF NOT EXISTS fatf.reports (
    id              SERIAL       PRIMARY KEY,
    doc_id          VARCHAR(256) REFERENCES public.documents(doc_id),
    report_type     VARCHAR(64)  NOT NULL,  -- recommendations | mutual_eval | guidance | typologies
    country_code    VARCHAR(8),             -- ISO code, NULL for global docs
    publication_year INTEGER,
    title           TEXT,
    pdf_url         TEXT,
    checked_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fatf_type    ON fatf.reports(report_type);
CREATE INDEX IF NOT EXISTS idx_fatf_country ON fatf.reports(country_code);

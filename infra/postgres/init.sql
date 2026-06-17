-- AIgent-squad Knowledge Base schema (spec 21)
-- pgvector + HNSW index for semantic search; FTS as fallback

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS kb_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type VARCHAR(32) NOT NULL CHECK (type IN ('troubleshooting', 'decision', 'pattern', 'infrastructure')),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT[] DEFAULT '{}',
    service_name TEXT,
    embedding vector(1024),  -- Bedrock Titan Embed v2: 1024 dims
    metadata JSONB DEFAULT '{}'::jsonb,
    confidence_score REAL NOT NULL DEFAULT 0.0,
    status VARCHAR(32) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'pending_review', 'superseded', 'rejected')),
    superseded_by UUID REFERENCES kb_items(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb_provenance (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_item_id UUID NOT NULL REFERENCES kb_items(id) ON DELETE CASCADE,
    source_investigation_id UUID NOT NULL,
    confidence REAL NOT NULL,
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    extractor_model TEXT,
    enricher_model TEXT
);

-- Indexes for fast lookup
CREATE INDEX IF NOT EXISTS idx_kb_items_status ON kb_items(status);
CREATE INDEX IF NOT EXISTS idx_kb_items_type ON kb_items(type);
CREATE INDEX IF NOT EXISTS idx_kb_items_service ON kb_items(service_name);
CREATE INDEX IF NOT EXISTS idx_kb_items_tags ON kb_items USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_kb_items_metadata ON kb_items USING GIN(metadata);

-- Full-text search fallback (when embedding similarity < threshold)
CREATE INDEX IF NOT EXISTS idx_kb_items_title_trgm ON kb_items USING GIN(title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_kb_items_content_trgm ON kb_items USING GIN(content gin_trgm_ops);

-- HNSW index for vector similarity (approximate but fast)
-- Uses cosine distance (matches Bedrock Titan output)
CREATE INDEX IF NOT EXISTS idx_kb_items_embedding ON kb_items
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_kb_provenance_kb_item ON kb_provenance(kb_item_id);
CREATE INDEX IF NOT EXISTS idx_kb_provenance_investigation ON kb_provenance(source_investigation_id);

-- Auto-update updated_at on changes
CREATE OR REPLACE FUNCTION trg_set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS kb_items_updated_at ON kb_items;
CREATE TRIGGER kb_items_updated_at
    BEFORE UPDATE ON kb_items
    FOR EACH ROW
    EXECUTE FUNCTION trg_set_updated_at();

-- Add chunk_index column to spans table
-- This field stores the chunk number for better error messages
ALTER TABLE spans ADD COLUMN chunk_index INTEGER;

-- Update schema version
INSERT INTO schema_version (version, applied_at) VALUES (2, datetime('now'));

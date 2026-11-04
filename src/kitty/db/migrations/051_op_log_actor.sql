-- Who made each block transaction (kitty.core.blocks.actor): op_log rows recorded only the
-- device. `actor` is user, agent, client, system or peer; `actor_name` its display name (the
-- MCP client, the paired client, the paired device, "migration", "compaction");
-- `actor_session` an agent's or client's session, which History filters by and "undo this
-- agent session" reverts; `actor_via` (JSON) the actors a peer merge carried from the other
-- device. Rows already written are classified from what they are: the block migrations' own
-- rows (op_log_migrated) are the system's, rows from another device are that peer's, the
-- compaction's Trash purges are the system's, and the rest are the person at this device.
-- The backfill is the only update op_log ever takes: the append-only trigger is lifted for
-- it and put back unchanged in the same transaction.
ALTER TABLE op_log ADD COLUMN actor TEXT NOT NULL DEFAULT 'user'
    CHECK (actor IN ('user', 'agent', 'client', 'system', 'peer'));
ALTER TABLE op_log ADD COLUMN actor_name TEXT;
ALTER TABLE op_log ADD COLUMN actor_session TEXT;
ALTER TABLE op_log ADD COLUMN actor_via TEXT;

DROP TRIGGER op_log_append_only;
UPDATE op_log SET actor = 'system', actor_name = 'migration'
WHERE seq <= COALESCE((SELECT last_seq FROM op_log_migrated WHERE id = 1), 0);
UPDATE op_log SET actor = 'peer', actor_name = (SELECT name FROM peers WHERE peers.device_id = op_log.device_id)
WHERE actor = 'user' AND device_id <> (SELECT device_id FROM block_history WHERE id = 1);
-- A compaction row purges expired Trash; the page revision bump rides along.
UPDATE op_log SET actor = 'system', actor_name = 'compaction'
WHERE actor = 'user' AND json_valid(CAST(ops AS TEXT))
  AND EXISTS (SELECT 1 FROM json_each(CAST(ops AS TEXT), '$.ops') AS item WHERE json_extract(item.value, '$.op') = 'purge')
  AND NOT EXISTS (SELECT 1 FROM json_each(CAST(ops AS TEXT), '$.ops') AS item
                  WHERE json_extract(item.value, '$.op') <> 'purge'
                    AND NOT (json_extract(item.value, '$.op') = 'set' AND json_type(item.value, '$.type') IS NULL
                             AND json_type(item.value, '$.props.revision') = 'integer'
                             AND (SELECT COUNT(*) FROM json_each(item.value, '$.props')) = 1));
CREATE TRIGGER op_log_append_only BEFORE UPDATE ON op_log
BEGIN SELECT RAISE(ABORT, 'op_log is append-only'); END;

CREATE INDEX op_log_actor_session ON op_log(actor_session, seq) WHERE actor_session IS NOT NULL;

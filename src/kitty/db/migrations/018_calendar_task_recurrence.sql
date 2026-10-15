-- A recurrence is a lineage of real task instances, never synthetic completions.
ALTER TABLE calendar_tasks ADD COLUMN repeat_anchor TEXT NOT NULL DEFAULT '';
ALTER TABLE calendar_tasks ADD COLUMN task_series_id TEXT REFERENCES calendar_tasks(id);
ALTER TABLE calendar_tasks ADD COLUMN previous_task_id TEXT REFERENCES calendar_tasks(id);

-- Undo tombstones an untouched successor; a later completion can replace it.
CREATE UNIQUE INDEX calendar_tasks_live_successor
ON calendar_tasks(previous_task_id) WHERE deleted = 0 AND previous_task_id IS NOT NULL;
CREATE UNIQUE INDEX calendar_tasks_pending_series
ON calendar_tasks(task_series_id)
WHERE deleted = 0 AND task_series_id IS NOT NULL
AND json_extract(data, '$.status') IN ('todo', 'doing');

UPDATE calendar_tasks SET data = json_set(data,
 '$.repeat_frequency', '', '$.repeat_interval', 1, '$.repeat_until', '');

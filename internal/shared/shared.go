package shared

import "example.com/guardrails/internal/storage"

func RecordID(record storage.Record) int { return record.ID }

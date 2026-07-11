export interface BackupSummary {
  name: string;
  size_bytes: number;
  created_at: string;
}

// Response shape of POST /backups (server/engine/backup.py::create_backup)
export interface BackupCreateResult extends BackupSummary {
  contents: string[];
}

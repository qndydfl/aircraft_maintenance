# Windows re-index worker

The `ManualPortal Reindex Worker` scheduled task starts at user logon and runs:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\run_reindex_worker.ps1
```

The worker processes queued `ReindexJob` rows continuously. Its output is appended
to `logs\reindex-worker.log`. Task Scheduler is configured to ignore duplicate
starts and restart the worker after a failure.

Useful commands:

```powershell
Get-ScheduledTask -TaskName "ManualPortal Reindex Worker"
Start-ScheduledTask -TaskName "ManualPortal Reindex Worker"
Stop-ScheduledTask -TaskName "ManualPortal Reindex Worker"
```

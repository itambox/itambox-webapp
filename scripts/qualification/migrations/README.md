# Migration qualification

Historical migration-rehearsal suites moved out of the normal `serial_only`
lane live here. They remain explicitly runnable:

```bash
PYTHONPATH=itambox pytest scripts/qualification/migrations/
```

They are wired into CI when migration files change and during release
qualification. Do not move genuine lock/concurrency coverage into this area.

# Thesis runner, status 30.10.26

## Done
- **Backup**: the pre-upgrade runner is `00-Archive\run_tests-2026-10-30-before-upgrade.py`.
- **provenance.py**: file hashes (requests, flow, tables, prompts, code, runner), versions, per-request GPU state.
- **run_tests.py (upgraded 30.10)**: `--app` (default live 02 - App), `--flow` (id or comma list), `--ids`, `--reps`, `--seed 2610`, `--warmup 5`, `--idle-seconds 30`, `--min-window 2`, `--note`. Dry by default, `--live` refused unless `test_calendar_id` is in the flow's settings table (it is not yet).
  - Per run a new log `<flow>-YYYYmmdd-HHMMSS.jsonl`: header line (versions, all hashes, combined hashes, args), then per repeat and flow: 5 discarded warm-up requests (`warmup: true`), a 30 s idle baseline with the model pinned (`keep_alive -1`), the requests shuffled with seed and rep, one `block` line with block energy.
  - Per request: counter J, 50 ms Zeus poll J, net J (gross minus idle W x window), window held open to 2 s (`short_window`), GPU temp/clocks/power/pstate/throttle before and after.
  - Stops with an `abort` line if any provenance hash changes between repeats.
- **calibrate_energy.py**: 30 s idle + 30 s load, counter vs Zeus poll vs nvidia-smi poll, writes `calibration.json`, counter primary only if within 5 %.
- **make_release.py**: copies runner, requests, flow, tables and app code into `Release\v1.0` (or `--target`), writes MANIFEST.sha256 and ENVIRONMENT.txt, refuses a non-empty target. Tested to a temp folder, deleted.

## Checked facts
- Dry mode still calls the model, so the dry warm-up loads qwen2.5:7b.
- papertrail sends no `keep_alive` and uses seed 1 in `ask_small`; the runner pins the model itself.
- settings.csv has `test_calendar,IU` but no `test_calendar_id`.

## Open
1. Run `calibrate_energy.py` on an idle GPU and put the result table in the method chapter.
2. Latin square across conditions once the other conditions have flows (`--flow a,b,c` already runs them in order per repeat).
3. Add `test_calendar_id` before any `--live` run.

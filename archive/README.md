# archive/ — kept for history, NOT used by the pipeline

Between 28-30 Sep 2026 a series of one-shot "patch via CI" workflows and base64
chunk restores were pushed to `main`. Four of the workflow files were invalid YAML
(each one produced a red "0s" failed run on every push to `main`: 71 failed runs
each between 30-Sep and 01-Oct-2026), the valid ones commit and push straight to
`main`, and they re-apply old board/TTS patches over the current code.

They were moved here (renamed `*.yml.txt` so GitHub Actions ignores them) instead of
being deleted. Nothing in the pipeline imports anything from this folder.
If you are sure you'll never need them, this folder can be deleted.

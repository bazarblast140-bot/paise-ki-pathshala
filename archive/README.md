# archive/ — kept for history, NOT used by the pipeline

Between 28-30 Sep 2026 a series of one-shot "patch via CI" workflows and base64
chunk restores were pushed to `main`. Four of the workflow files were invalid YAML
(every push showed 4-6 red "0s" failed runs), and the valid ones re-applied the
filler-narration TTS patch that made Shorts 190s+ long.

They were moved here (renamed `*.yml.txt` so GitHub Actions ignores them) instead of
being deleted. Nothing in the pipeline imports anything from this folder.
If you are sure you'll never need them, this folder can be deleted.

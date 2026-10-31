# Contributing

Suggestions, bug reports and pull requests are welcome. This project is a public portfolio piece from HackerRank Orchestrate (October 2026), and outside ideas on the design are especially useful.

## Ways to help

- **Suggest an improvement:** open an issue using the "Suggestion" template. Good topics: better record linking, tie-breaking, evidence parsing, evaluation ideas, docs.
- **Report a bug:** open an issue using the "Bug report" template with the command you ran and the output.
- **Ask a question or discuss design:** use the Discussions tab if it is enabled, otherwise an issue.
- **Send a pull request:** small, focused PRs are easiest to review.

## Running the project

You need the official challenge `dataset/` folder, which is not redistributed here. See the Quickstart in the [README](README.md). Everything runs without API keys: model answers are replayed from `code/cache/`.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r code/requirements.txt
python -m pytest -q code/tests
```

## Pull request guidelines

1. Keep the deterministic core deterministic: no hardcoded case, resource, user or evidence IDs in logic.
2. Evidence captured after a case's `audit_at` must never decide that case's state.
3. Add or update tests built from real dataset rows. The golden-output lock (`code/tests/test_golden_lock.py`) must still pass, or the PR must explain why the output legitimately changes.
4. Run `python code/main.py eval` and include the metric change in the PR description.
5. Never commit secrets. `.env` is ignored; use `.env.example` for placeholders.
6. Do not add the challenge dataset or problem statement to the repository.

Be respectful. Disagreement about design is welcome; personal attacks are not.

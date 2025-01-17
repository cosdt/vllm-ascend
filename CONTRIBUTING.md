# Contributing to vLLM Ascend backend plugin

## DCO and Signed-off-by

When contributing changes to this project, you must agree to the DCO. Commits must include a `Signed-off-by:` header which certifies agreement with the terms of the DCO.

Using `-s` with `git commit` will automatically add this header.

## Linting and formatting

```bash
pip install -r requirements-lint.txt

# 1. Do work and commit your work.
# 2. Format files that differ from origin/main.
bash format.sh
# 3. Commit changed files with message 'Run yapf and ruff'
git commit -sm "Run yapf and ruff"
```

## PR Title and Classification

Only specific types of PRs will be reviewed. The PR title is prefixed appropriately to indicate the type of change. Please use one of the following:

- `[Bugfix]` for bug fixes.
- `[CI/Build]` for build or continuous integration improvements.
- `[Doc]` for documentation fixes and improvements.
- `[Model]` for adding a new model or improving an existing model. Model name should appear in the title.
- `[Frontend]` For changes on the vLLM frontend (e.g., OpenAI API server, `LLM` class, etc.)
- `[Kernel]` for changes affecting CUDA kernels or other compute kernels.
- `[Core]` for changes in the core vLLM logic (e.g., `LLMEngine`, `AsyncLLMEngine`, `Scheduler`, etc.)
- `[Hardware][Vendor]` for hardware-specific changes. Vendor name should appear in the prefix (e.g., [Hardware][AMD]).
- `[Misc]` for PRs that do not fit the above categories. Please use this sparingly.

> [!NOTE]
> If the PR spans more than one category, please include all relevant prefixes.

## Others

You may find more information about contributing to vLLM on [<u>docs.vllm.ai</u>](https://docs.vllm.ai/en/latest/contributing/overview.html).

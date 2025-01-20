# Contributing to vLLM Ascend backend plugin

## DCO and Signed-off-by

When contributing changes to this project, you must agree to the DCO. Commits must include a `Signed-off-by:` header which certifies agreement with the terms of the DCO.

Using `-s` with `git commit` will automatically add this header.

## Testing

```bash
pip install -r requirements-dev.txt

# 1. linting and formatting
bash format.sh
# 2. Unit tests
pytest tests/
# 3. Commit changed files using `-s`
git commit -sm "your commit info"
```

## PR Title and Classification

Only specific types of PRs will be reviewed. The PR title is prefixed appropriately to indicate the type of change. Please use one of the following:

- `[Plat]` for new features or optimization in platform.
- `[Attn]` for new features or optimization in attention.
- `[Comm]` for new features or optimization in communicators.
- `[Model Runner]` for new features or optimization in model runner.
- `[Worker]` for new features or optimization in worker.
- `[Op]` for adding a new ops or improving an existing ops. Op name should appear in the title.
- `[Model]` for adding a new model or improving an existing model. Model name should appear in the title.
- `[Bugfix]` for bug fixes.
- `[Doc]` for documentation fixes and improvements.
- `[Tool]` for format scripts or other tools.
- `[UT]` for unit tests.
- `[CI]` for build or continuous integration improvements.
- `[Misc]` for PRs that do not fit the above categories. Please use this sparingly.

> [!NOTE]
> If the PR spans more than one category, please include all relevant prefixes.

## Others

You may find more information about contributing to vLLM Ascend backend plugin on [<u>docs.vllm.ai</u>](https://docs.vllm.ai/en/latest/contributing/overview.html).

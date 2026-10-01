# Contributing to Jig

Thank you for your interest in Jig. It is under active development, so issues, ideas and pull requests are all welcome.

## Before you start

- For anything larger than a small fix, please open an issue first so we can agree the approach.
- Security problems go through the private process in [SECURITY.md](SECURITY.md), not public issues.
- By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Development set-up

You need Python 3.11 or newer and a local OpenAI-compatible model server with tool calling (see [Model requirements](README.md#model-requirements)).

```powershell
git clone https://github.com/rlesueur/jig.git
cd jig
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\jig health
```

On macOS or Linux use `.venv/bin/` instead of `.\.venv\Scripts\`.

## Project principles

These apply to every change:

- **No mocks.** Tests run against the real configured model server, a real temporary SQLite database and, for the container tests, real Docker. Do not add mocks, fakes or stubbed responses.
- **No silent fallbacks.** If something fails, raise a clear error that says what went wrong and how to fix it. Do not quietly substitute a default, skip a step or return an empty result.
- **Model-agnostic.** Do not assume a particular model, prompt format or sampling scheme. Anything model-specific belongs in config or a profile.
- **Safety in code.** New tools must declare their effect (`read`, `private_write` or `side_effect`) and pass through the gate. A change must never loosen a core rule.
- **British English** in code comments, documentation, messages and user-facing text.

## Making a change

1. Create a branch from `main`.
2. Keep the change focused. Small pull requests are reviewed faster.
3. Add or update tests for the behaviour you change, and run them:

   ```powershell
   .\.venv\Scripts\python -m pytest -q
   ```

   Some tests need internet access, Docker with the sandbox image (`jig sandbox build`) or a vision-capable model; the [README](README.md#tests) explains which.
4. Update the README or other docs if behaviour, config or the API changes.
5. Write clear commit messages: a short summary line in the imperative mood, then detail if needed.
6. Open a pull request describing what changed, why, and how you tested it, including the model server and model you used.

## The avatar

The `<jig-avatar>` web component in `avatar/` has no dependencies and no build step. Open `avatar/index.html` through a local static server to try changes, and keep it within the performance and accessibility notes in [avatar/README.md](avatar/README.md).

## Licence

Jig is licensed under the [Apache License 2.0](LICENSE). By contributing, you agree that your contributions are licensed under the same terms.

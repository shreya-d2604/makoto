# makoto

*the code you're judged by*

![makoto banner](assets/banner.png)

Finds merged GitHub PRs, has an LLM judge which ones are most resume-worthy, and
injects them as bullet points into `resume.tex`.

Your local `resume.tex` (gitignored - see below) is the only source of truth.
Overleaf is a mirror, written to last, and only if you pass `--sync-overleaf`.

## Setup

```
pip install -r requirements.txt
```

Environment variables (never put these in `config.yaml`):

- `GITHUB_TOKEN` - a GitHub personal access token (`repo` or `public_repo` scope)
- `GEMINI_API_KEY` - a free Gemini API key from https://aistudio.google.com/apikey,
  used for PR ranking and description generation. Without it, the tool falls back
  to a deterministic ranking (log-scaled repo stars, then recency) and template-based
  descriptions - it still works end to end, just without LLM judgment.
- `OVERLEAF_PROJECT_ID` - only needed for `--sync-overleaf` (see below)

Edit `config.yaml` for your GitHub username, ranking rubric, pinned/excluded PRs,
and manual description overrides.

To avoid exporting these every session, add them to your shell profile once
(`~/.bashrc` for bash, `~/.zshrc` for zsh - check with `echo $SHELL`):

```
echo 'export GITHUB_TOKEN=your_token_here' >> ~/.zshrc
echo 'export GEMINI_API_KEY=your_key_here' >> ~/.zshrc
echo 'export OVERLEAF_PROJECT_ID=your_project_id_here' >> ~/.zshrc
source ~/.zshrc
```

Verify without ever printing the value: `echo -n "$GITHUB_TOKEN" | wc -c` should
print a number greater than 0.

## Set up your own resume.tex

`resume.tex` is gitignored and never committed to this repo - it's your personal
resume, this repo is a shared tool. You need to create it yourself at the repo
root, with these two marker lines on their own, wherever you want the PR bullets
to appear:

```latex
% PR-SECTION-START
% PR-SECTION-END
```

Anything between them gets fully replaced on each `inject` run; everything else
in the file is left untouched. If you don't have a LaTeX resume yet,
`tests/fixtures/sample_resume.tex` is a minimal example you can copy and build on.

## Typical workflow

```
# 1. Whenever you've merged new PRs, refresh the cache
python3 src/cli.py fetch

# 2. (optional) sanity-check the ranking before it lands in your resume
python3 src/cli.py rank --explain

# 3. (optional) preview the exact bullet text
python3 src/cli.py describe

# 4. write the bullets into resume.tex and compile-check it
python3 src/cli.py inject

# 5. (optional) also push the update to Overleaf
python3 src/cli.py inject --sync-overleaf
```

`fetch` + `inject` is the whole loop for routine use - `rank --explain` and
`describe` are just there to sanity-check the model's picks before committing to
them. `inject` is idempotent: if nothing changed since the last run, it says so
and does nothing.

## Usage

```
python3 src/cli.py fetch              # fetch merged PRs from GitHub, cache them
python3 src/cli.py rank               # rank cached PRs, show the selected top ones
python3 src/cli.py rank --explain     # show the full ranking with reasons
python3 src/cli.py rank --all         # show every merged PR found, chosen or not
python3 src/cli.py describe           # preview the generated resume bullets
python3 src/cli.py inject             # write bullets into resume.tex, compile-check, commit if tracked
python3 src/cli.py inject --dry-run   # preview the LaTeX without touching disk
python3 src/cli.py inject --sync-overleaf   # also mirror to Overleaf afterward
```

See "Set up your own resume.tex" above for the marker format `inject` looks for.

## Overleaf sync

`--sync-overleaf` uses [`pyoverleaf`](https://pypi.org/project/pyoverleaf/), an
**unofficial, reverse-engineered** client that authenticates using your default
browser's Overleaf session cookie (no API token needed - works on the free tier,
since Overleaf's official Git integration is a paid feature). Because it's
unofficial, it may break if Overleaf changes their internal web API.

Before overwriting, it downloads the current Overleaf copy of the file to
`.backup/` in case you need to recover something. If the sync fails for any
reason, it's reported as a warning, not a fatal error - your local `resume.tex`
is what matters, and you can always paste it into Overleaf by hand.

Requires `OVERLEAF_PROJECT_ID` to be set (see Setup above) - it's your project's
URL: `overleaf.com/project/<PROJECT_ID>`.

## Tests

```
python3 -m pytest tests/
```

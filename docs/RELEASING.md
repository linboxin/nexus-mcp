# Releasing to PyPI

The package is `union-nexus-mcp` (the name `nexus-mcp` is taken on PyPI). Users
run it with `uvx union-nexus-mcp …`; the command inside is still `nexus-mcp`.

## Recommended: trusted publishing (no token to handle)

One-time setup, once the repo is on GitHub:

1. On pypi.org → your account → **Publishing** → *Add a new pending publisher*:
   - PyPI project name: `union-nexus-mcp`
   - Owner: `linboxin`, Repository: `nexus-mcp`
   - Workflow name: `publish.yml`
   - Environment name: `pypi`
2. On GitHub → repo → Settings → Environments → **New environment** named `pypi`
   (optionally require your approval for deployments).

Every release afterwards:

```bash
# bump version in pyproject.toml and src/nexus_mcp/__init__.py, commit, then:
git tag v0.1.0 && git push origin main --tags
```

The `Publish to PyPI` workflow runs the tests, builds the wheel and sdist, and
uploads them. Check the Actions tab; the first run registers the project.

## Alternative: publish from your machine with an API token

1. pypi.org → Account settings → **API tokens** → *Add API token*, scope
   "Entire account" for the very first upload (project-scoped afterwards).
2. In your own terminal (never paste the token into chat or a file that is
   committed):

```bash
cd ~/Documents/Coding/nexus-mcp
uv build
UV_PUBLISH_TOKEN='pypi-…' uv publish
```

## Verify

```bash
uvx --refresh union-nexus-mcp --version
uvx union-nexus-mcp clients
```

## Checklist before tagging

- `uv run pytest` green, `uv build` succeeds
- version bumped in `pyproject.toml` and `src/nexus_mcp/__init__.py`
- README setup instructions still match the CLI

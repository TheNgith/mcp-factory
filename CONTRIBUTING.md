# Contributing to MCP Factory

## Development Setup

### Prerequisites
- **Python 3.10+** — the API uses union types (`bytes | None`), `dict[str, Any]` generics,
  and `match` statements. Python 3.6 will not work.
- **.NET 8 SDK** — required only for the Aspire local orchestration (`aspire/AppHost`)
- **git** for version control

### Initial Setup (API)

```powershell
# 1. Clone the repository
git clone https://github.com/evanking12/mcp-factory.git
cd mcp-factory

# 2. Create and activate virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# 3. Install API dependencies
pip install -r api/requirements.txt

# 4. Run the API locally
uvicorn api.main:app --reload --port 8000
```

> **Note:** `scripts/run_fixtures.ps1` and `scripts/smoke_test.ps1` are Section 2-3
> era scripts for the static discovery engine (`src/discovery/`), not the API.
> Do not use them to validate API changes.

## Workflow

### Branch Strategy

```bash
# Create feature branch from main
git checkout -b feature/your-feature-name

# Make changes and test locally
.\scripts\run_fixtures.ps1

# Commit with clear messages (see below)
git add .
git commit -m "feat(discovery): add COM type library parser"

# Push and create PR
git push origin feature/your-feature-name
```

### Commit Message Format

Follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation only
- `test:` Adding or updating tests
- `refactor:` Code restructuring without behavior change
- `perf:` Performance improvements
- `chore:` Maintenance tasks

**Scope (optional):** `feat(discovery):`, `fix(mcp):`, `docs(readme):`

**Examples:**
```
feat(discovery): add PDB parsing for type information
fix(fixtures): correct sqlite3 header matching regex
docs(sections-2-3): update coverage details
test(smoke): add COM object detection validation
```

## Code Standards

### Python (src/discovery/)
- Follow PEP 8 style guidelines
- Add docstrings for functions
- Include type hints where applicable
- Test with fixture DLLs before committing

### PowerShell (scripts/)
- Use approved verbs (Get-, Set-, Invoke-, etc.)
- Add comment-based help blocks
- Test on both PowerShell 5.1 and 7
- Validate on clean machine when possible

### Documentation
- Update README.md for user-facing changes
- Add entries to `docs/copilot-log.md` for AI-assisted work
- Keep `docs/sections-2-3.md` in sync with implementation

## Testing

### Automated Tests (API)

```powershell
# Validate Python syntax across all changed files
python -c "import ast; [ast.parse(open(f, encoding='utf-8').read()) for f in ['api/explore.py','api/main.py','api/chat.py']]; print('OK')"

# Run the full CI pipeline against the live Azure endpoint
.\sessions\ci-run.ps1 `
    -ApiUrl  "https://mcp-factory-pipeline.icycoast-8ddfa278.eastus.azurecontainerapps.io" `
    -ApiKey  "<key>" `
    -DllPath "C:\path\to\contoso_cs.dll"
```

### Manual Testing
- Test against `contoso_cs.dll` using `ci-run.ps1`
- Check PowerShell 5.1 compatibility for any `.ps1` script changes
- See `sessions/README.md` for full test suite and scoring rubric

## Pull Request Guidelines

### Before Submitting
1. Run fixture tests and verify they pass
2. Update relevant documentation
3. Add tests for new features
4. Rebase on latest main if needed

### PR Description Template
```markdown
## What
Brief description of the change

## Why
Problem this solves or feature it adds

## Testing
- [ ] Fixture tests pass
- [ ] Smoke tests pass
- [ ] Tested on clean environment (if applicable)

## Screenshots/Output
(If relevant - show before/after CSV, terminal output, etc.)
```

### Review Process
1. At least one team member approval required
2. All CI checks must pass (when configured)
3. Lead maintainer (Evan) final review for sections 2-3
4. Squash merge to main after approval

## Project Structure

```
mcp-factory/
├── api/                    # FastAPI backend: discovery, explore, chat, generate
│   ├── main.py             # FastAPI routes
│   ├── explore.py          # Explore orchestration + mini-session worker
│   ├── explore_prompts.py  # LLM prompt builders + gap question generation
│   ├── explore_vocab.py    # Vocab table management
│   ├── explore_phases.py   # Sentinel calibration, write-unlock probe
│   ├── chat.py             # Agentic SSE chat loop
│   ├── executor.py         # DLL/CLI/GUI/bridge dispatch
│   └── storage.py          # Blob Storage helpers
├── ui/                     # FastAPI web UI (proxies /api/* to pipeline)
├── src/discovery/          # Section 2-3: Static binary analysis (legacy)
├── sessions/               # CI scripts, test data, run archives
│   ├── ci-run.ps1          # Full headless pipeline runner
│   ├── contoso_cs/         # Test binary config and test suite
│   └── _runs/              # Archived session snapshots
├── infra/                  # Bicep infrastructure (ACA, Storage, Key Vault)
├── aspire/                 # .NET Aspire local orchestration
├── scripts/                # Section 2-3 era automation (PowerShell)
├── tests/                  # pytest tests
└── artifacts/              # Generated outputs (gitignored)
```

## Questions or Issues?

- **GitHub Issues:** For bugs, feature requests, or questions
- **Discussions:** For design discussions or architecture questions
- **Contact:** Tag @evanking12 for urgent items

## Resources

- [Model Context Protocol Specification](https://modelcontextprotocol.io/)
- [vcpkg Documentation](https://vcpkg.io/)
- [DUMPBIN Reference](https://learn.microsoft.com/en-us/cpp/build/reference/dumpbin-reference)
- [Project Docs](./docs/)

---

**Note:** This is an active capstone project with weekly iterations. Expect frequent updates to structure and guidelines.



## Development Notes

This project leverages GitHub Copilot for productivity. See `docs/copilot-log.md` 
for details on AI-assisted development decisions.
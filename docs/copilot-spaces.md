# Copilot Spaces Context Guide

**Purpose:** This document tells Copilot (and team members) how to read and understand the mcp-factory documentation ecosystem.

**Use this before committing:** Reference this guide to ensure your documentation updates are consistent and complete.

---

## Documentation Ecosystem

### 1. **copilot-log/entries.md** — Daily Work Log
**Purpose:** Record every Copilot interaction, what was built, what changed.  
**Cadence:** After each work session with Copilot  
**Audience:** Archival record, basis for lab-notes later

**REQUIRED Format (exact structure):**
```markdown
## [DATE] — [Feature/Task Name]

**Task/Issue:** [What problem were we solving?]

**Copilot Prompts Used:**
- "Prompt 1 that generated expected output"
- "Prompt 2 that generated expected output"
- "Prompt 3 that generated expected output"

**Output Accepted:**
- List of files/code Copilot generated
- What worked from the response
- What you accepted as-is

**Manual Changes:**
- List of edits you made after Copilot generated code
- Bug fixes, adjustments, refinements
- Why each change was needed

**Result:**
- Command(s) to reproduce
- Expected outputs (file names, sizes, sample data)
- Test results/metrics

**Notes:**
- Important implementation details
- Edge cases handled
- Known limitations

**References:**
- Related ADRs, commits, docs
- External documentation links
```

**Why this format:**
- **Prompts Used** (not just "Prompt"):  Show the exact prompts that yielded the result. Future you/teammates can re-run these.
- **Output Accepted + Manual Changes**: Separate the Copilot work from your work. Clear accountability.
- **Result + Notes**: Make reproducibility explicit. Show the happy path and caveats.

**Recent Entries:**
- 2026-01-19: Fixture Harness + Robust Parser
- 2026-01-20: 8-Module Refactoring + CLI Orchestration
- 2026-01-20: Production Polish + Developer Experience
- 2026-01-21: Confidence Scoring + Frictionless Setup (12 commits)
- 2026-02-22: §2.a Directory Walk — Installed-Instance Target
- 2026-02-22: Mac Compatibility Guide + Windows Unicode Fix
- 2026-02-27: §4 /chat Endpoint + §5 Chat UI + CREATE_NO_WINDOW Suppression
- 2026-03-04: GUI Pipeline Fixes — Exe Launch, UIA-First Backend, Open-File Action

---

### 2. **lab-notes.md** — Implementation Narrative
**Purpose:** Weekly summary of what was built, how it works, why decisions were made.  
**Cadence:** Weekly (or after major feature completion)  
**Audience:** Team, Microsoft, future developers

**Structure:**
```markdown
## [DATE]: [Feature Name]

**Goal:** [What was the objective?]

**Work done:**
- [Bullet list of implementations]

**Test Results:**
- [What was verified]
- [Metrics/accuracy numbers]

**Files Modified:**
- `file.py`: +X lines (description)
- `file.ps1`: +Y lines (description)

**Design Documentation:**
- ADR-XXXX: [Link to decision document]

**Next Steps:**
- [What's next]
```

**Current Status:**
```
## 2026-01-19: Iteration 1 Foundation ✅
## 2026-01-20: Modular Architecture Design ✅
## 2026-01-21: Confidence Scoring + Frictionless Deployment ✅
```

---

### 3. **adr/** — Architecture Decision Records
**Purpose:** Document major design decisions, alternatives, trade-offs.  
**Cadence:** When making architectural choices (not every feature)  
**Audience:** Team, reviewers, future maintainers

**Structure:**
```markdown
# ADR XXXX: [Title]

**Date:** YYYY-MM-DD  
**Status:** ACCEPTED / PROPOSED / SUPERSEDED  
**Owner:** [Name]

## Problem Statement
[What problem are we solving?]

## Decision
[What did we decide?]

## Rationale
[Why this over alternatives?]

## Alternatives Considered
- Alternative 1: [Why rejected]
- Alternative 2: [Why rejected]

## Implementation
[How to implement this]

## Consequences
[Positive and negative impacts]

## Verification
[How was this tested/verified?]

## References
[Links to related code, commits, docs]
```

**Current ADRs:**
- **ADR-0001:** Initial Scope (project boundaries)
- **ADR-0002:** Modular Analyzer Architecture (8-module design)
- **ADR-0003:** Frictionless UX & Confidence Analysis
- **ADR-0004:** JSON Output Schema v2.0 & Production Validation
- **ADR-0005:** Hybrid Analysis & Artifact Hygiene
- **ADR-0006:** Spec Gap Closure — Legacy Protocol Analyzers
- **ADR-0007:** Directory Scan — §2.a Installed-Instance Target
- **ADR-0008:** GUI Pipeline — Exe Launch, UIA-First Backend, Open-File Action

---

### 4. **sections-2-3.md** — Section Status & Scope
**Purpose:** Live document showing what's complete, in-progress, limitations for this section.  
**Cadence:** Updated when scope changes or major milestones reached  
**Audience:** Team, project sponsors

**Structure:**
```markdown
# Sections 2–3: [Section Name]

**Status:** [In Progress / Complete / Blocked]

## What's Complete ✅
- [Feature 1]
- [Feature 2]

## In Progress 🚀
- [Feature 3]

## What's Not Supported Yet ❌
- [Future feature]

## Test Results
| Metric | Value |
|--------|-------|
| Header match | 98.4% |
| Confidence HIGH | 8 exports |

## Known Limitations
- [Limitation 1]
- [Limitation 2]

## Next Iteration Scope
- [What's next]
```

**Current Status (2026-01-21):**
- Status: ✅ Complete
- 187 zstd exports analyzed (98.4% header match)
- 294 sqlite3 exports analyzed (95.9% header match)
- Confidence scoring: HIGH/MEDIUM/LOW tiers
- Frictionless one-command setup: verified on 2+ machines

---

### 5. **meeting-notes/** — Team & Sponsor Meetings
**Purpose:** Record decisions, guidance, action items from meetings.  
**Cadence:** After each meeting with team or sponsors  
**Audience:** Team alignment, decision audit trail

**Structure:**
```markdown
# Meeting: [Title]
**Date:** YYYY-MM-DD  
**Attendees:** [Names]

## Discussion
- [Key topic 1]
- [Key topic 2]

## Decisions Made
- [Decision 1]
- [Decision 2]

## Action Items
- [ ] Item 1 (Owner: Name)
- [ ] Item 2 (Owner: Name)

## Next Meeting
[Date/Time if scheduled]
```

---

## Commit Message Conventions

**For Copilot-assisted work:**
```
feat: [description]
  - What was built
  - Why it matters
  - Related ADR/issue (if applicable)
```

**Recent Examples:**
```
feat: add confidence scoring with color-coded output
  - Implements 6-factor confidence analysis
  - Enables Section 4 to prioritize exports
  - Related: ADR-0003

fix: guard repo-local vcpkg check against empty RepoRoot
  - Prevents "empty string" error on fresh machines
  - Tested on 2+ clean Windows installations
```

---

## How Copilot Spaces Uses This

When you create a Space with this repo, Copilot will:

1. **Index all docs** (automatic)
   - Reads entries.md, lab-notes.md, ADRs, sections-2-3.md
   - Builds knowledge graph of decisions + implementations

2. **Use as context** when you ask questions
   - "What is confidence scoring?" → reads ADR-0003 + lab-notes
   - "How does the discovery pipeline work?" → reads sections-2-3.md + architecture.md

3. **Understand team roles**
   - Evan: Sections 2-3 (discovery, confidence)
   - Layalie & Caden: Section 4 (MCP generation)
   - Thinh: Section 5 (verification UI)

4. **Remember decisions**
   - ADRs persist across conversations
   - Lab-notes provide implementation history
   - Entries.md shows exact prompts that worked

---

## Before You Commit: Checklist

**If building a new feature:**
- [ ] Code works (tested locally)
- [ ] Add entry to `docs/copilot-log/entries.md` (if Copilot-assisted)
- [ ] Update `docs/lab-notes.md` if major feature
- [ ] Create ADR if architectural decision
- [ ] Update `docs/sections-2-3.md` if scope changes
- [ ] Reference this guide to ensure consistency
- [ ] Commit with descriptive message

**If starting new section (Section 3, 4, or 5):**
- [ ] Create new ADR for section-specific decisions
- [ ] Create section status file (e.g., `docs/sections-4.md`)
- [ ] Add entry to `docs/lab-notes.md`
- [ ] Update `docs/copilot-spaces.md` if new docs added

---

## Quick Links

| Document | Purpose | Update Frequency |
|----------|---------|------------------|
| [entries.md](copilot-log/entries.md) | Daily work log | Per session |
| [lab-notes.md](lab-notes.md) | Weekly narrative | Weekly |
| [adr/](adr/) | Design decisions | As needed |
| [sections-2-3.md](sections-2-3.md) | Section status | As milestones hit |
| [architecture.md](architecture.md) | System design | On design changes |
| [product-flow.md](product-flow.md) | User journey | On flow changes |

---

## Example: Using This Guide Before a Commit

**Scenario:** You just built .NET reflection discovery for Section 3.

**Before committing:**

1. Read this guide → Understand what goes where
2. Add to `entries.md` → Record Copilot prompts used
3. Add to `lab-notes.md` → Implementation summary
4. Create `adr/0004-dotnet-reflection.md` → Design decision
5. Create `docs/sections-3.md` → Section status
6. Verify this guide is still accurate
7. Commit all docs + code together
8. Push to GitHub

**Result:** Copilot Spaces immediately understands:
- What you built (entries.md)
- Why you built it that way (ADR-0004)
- How it works (lab-notes.md)
- What's complete (sections-3.md)

Team can jump in immediately without questions. ✅

---

## Contributing to This Guide

If the documentation structure changes, update this file to reflect:
- New doc types added
- Cadence changes
- New commit conventions
- Changes to team structure

Keep this as the **source of truth** for how the team documents its work.

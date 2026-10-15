# Second audit pass — 2026-07-18 (archived)

These seven files are an **independent second write-up** of the 2026-07-18 audit of `main` at
`dc969264`. Until 2026-10-15 they sat at the repository root and shadowed the canonical
`docs/GESH_FINANCE_*.md` set, which is the one that was reconciled on 2026-08-04 and carries the
status columns.

- **Canonical:** `docs/GESH_FINANCE_*.md`. Update status there, not here.
- **Never reconciled:** nothing here was revised after 2026-07-19. Treat every "open" item as a
  point-in-time observation and verify against `GAPS_AND_IMPROVEMENTS_*.md` and git history.
- **ID collision:** `SEC-nn` here and `SEC-nn` in `docs/GESH_FINANCE_SECURITY_REVIEW.md` are
  *different findings* with the same numbers. Cite the file path with the ID.
- **Defect register:** the second-pass `GFD-nnn` register was *merged* (not archived) into
  `docs/GESH_FINANCE_DEFECT_REGISTER.md`, because `GFD-008` / `GFD-026` are referenced from code,
  tests and PR #238.

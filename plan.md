# Anki integration architecture assessment

## Problem and confirmed approach

Determine where learning-content generation belongs relative to `ilias-sync`,
which is a Windows Node.js CLI with a sync pipeline, per-course output folders,
persisted sync state, and an optional background daemon.

The confirmed approach is to build a separate standalone companion app that
consumes the local course folders and sync metadata produced by `ilias-sync`.
`ilias-sync` remains responsible for ILIAS login, crawling, downloading, and
tracking file synchronization. The companion app is responsible for extracting
content, generating study material, and delivering cards to Anki.

Anki generation should not initially be part of the core sync path or require
a general plugin system. A future thin command, event feed, or adapter may
connect the two applications without moving learning logic into the downloader.

## Decision and completed assessment

- **Core functionality:** Rejected initially. It would tightly couple
  downloading to parsing and card generation, increasing failure modes,
  dependencies, testing scope, and maintenance cost.
- **In-process plugin system:** Rejected initially. It offers extensibility,
  but plugin discovery, lifecycle, compatibility, permissions, and error
  isolation add infrastructure before multiple integrations are needed.
- **Standalone companion app:** Selected. It has a clear boundary, can evolve
  independently, and can consume ordinary local files without duplicating
  ILIAS authentication or crawling.

The initial scope is deterministic processing of downloaded non-video material,
including PDFs, documents, slide decks, and HTML where practical. Anki delivery
should support both AnkiConnect for automatic insertion and `.apkg` export for
manual import or portability. Direct writes to Anki's database should be
avoided.

## Notes and considerations

- Current outputs are ordinary local files; the sync result already exposes
  added/updated absolute paths, which could support an integration later.
- Card generation is a separate concern from downloading: it may require PDF
  text/OCR, slide semantics, summarization, deduplication, deck/note mapping,
  and review-safe updates.
- Anki integration should use AnkiConnect over localhost or `.apkg` export;
  direct Anki database writes should be avoided.
- The first implementation should be a small deterministic MVP. AI-assisted
  generation can be added later with explicit review and privacy controls.
- No repository code should be changed for this architecture discussion.
- The boundary should leave room for later video digests, AI-assisted
  generation, knowledge graphs, and other derived-learning features without
  coupling those concerns to the downloader.
- The first integration can rely on stable local files and
  `.ilias-sync-state.json`; direct app-to-app communication can be introduced
  only when it provides a concrete UX benefit.

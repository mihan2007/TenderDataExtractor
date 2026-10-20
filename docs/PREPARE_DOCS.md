# Preparing the bid documents — preparation plan

Read after `docs/PARTICIPATION.md` when the user confirms that the bid documents should be prepared. Use the data already collected: Blocks 1–7 (extracted per `CLAUDE.md`), the checklist of bid content and bidder requirements (`docs/PARTICIPATION.md`), your company's details (`COMPANY.md`) and the register of corporate documents (`CORPORATE_DOCS.md`). Don't ask for the folder or files again.

## Corporate documents (copying ready files)

If a line of the bid content checklist matches a document type in `CORPORATE_DOCS.md` (articles of association, trade-register extract, tax certificate, ISO certificate, ...):

- the register names a file → copy it from `CorporateDocs/` (in the project folder) to `<tender folder>/Bid/` (create the folder if needed); in the plan, mark the line "Ready file — copied" instead of "to fill in";
- the register says `‹add›` for that type → don't invent a source: list the line under "Missing data" and ask the user where to get the document.

## Task

Build and show a step-by-step plan for preparing the bid. **At this step nothing is filled in and no files are created automatically** — only what has to be done, and in which order.

## What the plan contains

1. **Forms / documents to prepare** — from the bid content checklist and the templates actually found in the tender folder (bid form, price schedule, declarations, questionnaire, ...). For each: the template file, what goes into it, whether the data is available (from `COMPANY.md` / the tender documents) or has to come from the user.
2. **Missing data** — information needed for a form that is in neither `COMPANY.md` nor the tender documents (a signatory's power of attorney, a bid guarantee from the bank, a certificate, references, ...) — list explicitly what the user must provide.
3. **Order and deadlines** — the sequence of work tied to the dates of Block 4 (submission deadline etc.), so it is clear what must be ready by when. Allow time for documents that take days to obtain (bank guarantees, certified translations, apostilles, official certificates).
4. **Next step** — at the end ask whether the user is ready to start filling in, and with which form.

## Output format

**Bid preparation plan**
| No. | Document / form | What to fill in | Data source | Status |
|---|---|---|---|---|

**Missing data** (if any)
- ...

**Deadlines**
- ...

Then in the chat: "Ready to start filling in? Tell me which form to begin with."

## Filling in a form (on request)

Fill a form only after the user explicitly asks for that form — never auto-fill all forms at once.

When the user names a form:

1. Determine the template's type (`.docx` / `.xlsx`) and use the matching skill.
2. Fill it with your company's details (`COMPANY.md`) and the tender data (Blocks 1–7).
3. If data is missing — ask the user specifically, only for the missing field.
4. Save the filled file in the tender's `AIExtract/` folder as `<original name>_filled.docx` / `.xlsx`.
5. Send the file to the user.

## Constraints

- At the planning stage — create and fill nothing, only show the plan.
- Never invent data that is neither in the tender documents nor in `COMPANY.md`.
- Every field is filled with real data only. If data is missing, leave the field empty in the document that goes to the buyer (no "[to clarify]" placeholders in the bid itself) and list everything missing to the user in the chat, so the form can be completed and updated.
- Statements in the bid must be true. If supporting evidence for a statement (e.g. a reference contract, a certificate) is missing, tell the user before the form is finalized instead of glossing over it.

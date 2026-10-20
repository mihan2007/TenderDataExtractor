# Bidding in a tender — checking the bid requirements

Read after step 9 of `CLAUDE.md` when the user confirms they will bid. Use the files already read in step 4 and the data of Blocks 1–7 — don't ask for the folder again.

If forms or the checklist need your company's details (registration number, VAT number, address, bank details, managing director, ...), take them from `COMPANY.md` in the project folder — don't ask the user again.

## Task

Find two kinds of information in the tender documents and show them in the chat, verbatim or faithfully summarized, each with its source (file + clause / section):

1. **Bid content requirements** — which documents, information and forms the bid must contain (split into the parts the documentation uses — e.g. technical / financial envelope, first / second part — or one list if it is a single-part bid).
2. **Bidder requirements** — eligibility and qualification conditions: legal capacity, no exclusion grounds (insolvency, convictions, unpaid taxes, debarment lists), economic and financial standing (turnover, ratios), technical and professional ability (experience, references, certifications, licences, staff), SME status, any origin rules — only what the documents actually require.

## Where to look

The documents already read (notice, instructions to bidders, data sheet, technical specification, draft contract), first of all sections named like: "Qualification criteria", "Eligibility", "Exclusion grounds", "Selection criteria", "Content of the bid / tender", "Submission of tenders", "Bid data sheet", "Instructions to bidders".

If the requirements are split by bid part, keep the split. If there is no explicit section, go through the whole text for scattered requirements.

## Output format

Two tables in the chat:

**Bid content requirements**
| No. | Requirement | Source (file, clause) |
|---|---|---|

**Bidder requirements**
| No. | Requirement | Source (file, clause) |
|---|---|---|

If nothing is found for one of them, say so: "No [bid content / bidder] requirements were found in the provided documents" — never fill in "typical" requirements yourself.

## Constraints

- Only what is actually written in the documents — no "standard" sets from experience unless the text confirms them.
- Every line with its source (file + clause / section, if any).
- No guarantees of admission — this is a factual summary of the documentation, not legal advice.

## After the checklist — ask about preparing the documents

Right after showing both tables ask:

> "Shall we start preparing the bid documents?"

- No / no answer / the user moves on → stop here.
- Yes → read `docs/PREPARE_DOCS.md` and follow it strictly, using the data already collected (Blocks 1–7, this checklist, `COMPANY.md`) — ask for nothing again.

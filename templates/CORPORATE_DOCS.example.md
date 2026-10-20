# Corporate documents — file register

Copy this file to `CORPORATE_DOCS.md` in the project root (git-ignored). The files themselves go into `CorporateDocs/` (also git-ignored).

This register maps "document type → exact file", so that bid preparation (`docs/PREPARE_DOCS.md`) takes the exact path instead of guessing from file names. Update the table by hand when files are added or replaced.

| Document type | File (in CorporateDocs/) | Comment |
|---|---|---|
| Articles of association / incorporation | `‹add›` | |
| Trade / company register extract | `‹add›` | Many buyers require one not older than 3–6 months |
| Tax registration / VAT certificate | `‹add›` | |
| Certificate of no tax arrears | `‹add›` | Usually has a short validity |
| Social-security contributions certificate | `‹add›` | |
| Appointment of the managing director | `‹add›` | |
| Power of attorney for the bid signatory | `‹add›` | Needed when someone other than the legal representative signs |
| Financial statements (last 2–3 years) | `‹add›` | |
| Bank reference / statement of financial standing | `‹add›` | |
| Insurance certificate (liability) | `‹add›` | |
| ISO 9001 / other certificates | `‹add›` | |
| Licences / permits | `‹add›` | |
| SME declaration | `‹add›` | |
| Self-declaration on exclusion grounds (e.g. ESPD in the EU) | `‹add›` | |
| Register of reference contracts (experience) | `‹add›` | Main source for experience / reference forms |

Add rows when buyers ask for new document types.

## How it is used

In `docs/PREPARE_DOCS.md`, for each line of the bid content checklist (`docs/PARTICIPATION.md`) that matches a type in this register, the file is copied from its exact path into `<tender folder>/Bid/` instead of being searched for again.

`‹add›` next to the required type is an explicit signal to ask the user where to get the document — never guess.

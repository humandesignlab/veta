"""Output formatters (spec section 3.5, step 5).

Renders the annotated shortlist as console intelligence cards and as XLSX
(one row per tender with intelligence columns). No em dashes in any output.

TODO(phase1): implement formatters.
  - render_console(tenders) -> str  (intelligence card per tender)
  - write_xlsx(tenders, path)  (openpyxl, one row per tender)
"""

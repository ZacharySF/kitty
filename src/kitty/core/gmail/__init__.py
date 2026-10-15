"""Stage 6: read-only Gmail checks that propose application status changes.

The pieces, in the order a check runs them:
  * auth:      OAuth with the gmail.readonly scope only; token in data/.
  * fetch:     list and read recruiting-looking messages through the Gmail REST API.
  * classify:  rules (no model) that spot rejection / OA / interview / offer mail.
  * match:     tie a message to exactly one open application by company name.
  * check:     the run itself; every hit becomes a status_events row with
               state='proposed', which changes nothing until I confirm it.
  * proposals: the pending proposals, joined with company and title for review.
"""

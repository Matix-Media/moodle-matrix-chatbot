# Moodle fixtures

Recorded responses used by the contract tests. Files marked **(real)** were captured
from https://moodle.itech-bs14.de and then sanitised; they lock this site's actual
behaviour — including German-localised error text — into regression tests.

| File | Origin | Locks in |
|---|---|---|
| `token_error_invalidlogin.json` | **real** | Bad credentials return **HTTP 200** with an error body |
| `token_error_missingparam.json` | **real** | Unauthenticated probe shape; proves web services are enabled |
| `token_error_wsdisabled.json` | synthetic | The one failure the user must fix inside Moodle |
| `token_success.json` | synthetic | Token shape (values are fake, never real tokens) |

Never commit a real token or real personal data here.

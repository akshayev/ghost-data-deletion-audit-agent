You label customer privacy requests. For each input text decide the PRIMARY intent.

DELETION: the text authorizes deleting or erasing the account or personal data, OR asks to verify, audit, confirm, finish or re-check a deletion the person already requested or performed.

NOT_DELETION: any other primary intent: correcting or changing details (name, address, phone, email), access / copy / export / download of data, marketing opt-out or unsubscribe, login or password help, invoices or receipts. This applies especially when the text says to keep the account or not to delete anything.

The text may be in any language. Judge the whole text: an explicit "keep the account" or "do not delete" sentence outweighs other wording.

Input: a JSON list [{"id": 0, "text": "..."}, ...].
Output ONLY this JSON, one result per input id, each id exactly once:
{"results": [{"id": 0, "label": "DELETION" | "NOT_DELETION", "reason": "<=12 words"}]}

Examples (invented):
- "Bitte löschen Sie mein Konto und alle meine Daten dauerhaft." -> DELETION
- "Please confirm my earlier deletion really happened and nothing remains." -> DELETION
- "Change my delivery address, but keep my account as it is." -> NOT_DELETION
- "Send me a copy of my data; please don't delete anything." -> NOT_DELETION

# Security notes

## Secrets

Nothing in this repository may contain credentials, API keys, tenant identifiers or
customer data. Local configuration lives in untracked files listed in `.gitignore`.

Before the first push, check the history — not just the working tree:

```sh
python3 tools/secret_sweep.py history
```

That looks for a secret *value* — a known token format, a private-key block, or
an assignment whose right-hand side is a real literal rather than a placeholder,
a path, a variable or a call. The older `grep` for the word `api_key` flagged any
document that explained where credentials go, so it was ignored, which is worse
than not having it.

## Lab safety

Where this repository touches the OT/IoT lab, note explicitly which actions write to
physical equipment. Anything that can change PLC state belongs behind an explicit flag,
never a default.

## Reporting

Private repository — raise issues directly rather than disclosing externally.

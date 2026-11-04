# Synthetic contract examples

Every record here has `source_kind: synthetic_fixture`. The apply result and verification event explicitly say `execution: simulated`. These records do not describe measured traffic or prove Linux enforcement.

`valid/` contains nine records covering eight types, including separate upload/download estimates. The low-demand estimates remain unavailable while configured initial shaper rates remain separately identified. A user correction and a 30-second intent affect the example service treatment without overwriting the original classifier prediction.

`invalid/index.json` identifies deliberately invalid records and their expected validation errors. The checker must reject every one for the specified reason. The linked replay uses an injected clock to check expiry and stale observations without sleeping or calling networking commands.

After installing the package, run from the repository root:

```bash
python -m adaptive_qos validate-config --config-dir configs
python -m adaptive_qos check-contracts --examples examples/contracts --config-dir configs
python -m adaptive_qos schema
```

To regenerate these deterministic examples after an intentional schema change, run `python examples/contracts/generate.py`. Review changes and rerun tests; do not use regeneration to hide a failing contract.

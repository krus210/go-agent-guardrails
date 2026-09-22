# golangci-lint 2.10 configuration schema

`golangci.v2.10.jsonschema.json` was saved from the [official source](https://golangci-lint.run/jsonschema/golangci.v2.10.jsonschema.json) on 16 September 2026 so that `golangci-lint config verify --schema` can run offline.

SHA-256: `497d62a33cfcfec1061e8e5de33d53cf1b79213ff84b3061e106c8a377758f1b`.

The verification script passes an absolute `file://` URI of this schema. The schema catches unknown keys, but it does not prove that a pattern selects the intended types or that a rule covers the intended packages. Separate examples with known violations check that.

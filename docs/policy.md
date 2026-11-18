# Policy reference

Two files. The policy says what an agent may do. The manifest says how to
read a tool call. Anything not granted is denied.

```yaml
version: 1
org:
  name: Northwind Books
  unit: Accounts payable
roles:
  - name: finance-reader
    tools: [ledger.read, ledger.query, files.read]
    scopes:
      files:
        allow: ["data/finance/**"]
        deny: ["data/finance/**/*.raw"]
      tables:
        allow: [finance.invoices, finance.vendors]
  - name: finance-analyst
    inherits: finance-reader
    tools: [ledger.transfer, email.send, http.get]
    spend:
      currency: USD
      per_call: 1000
      per_day: 2500
      total: 10000
    recipients:
      domains: [northwind.example]
      addresses: [ap@northwind.example]
    scopes:
      urls:
        allow: ["https://ledger.internal.example/**"]
    approvals:
      - name: large-transfer
        tools: [ledger.transfer]
        when: {arg: amount_usd, gt: 700}
        reason: Transfers above $700 need a person.
agents:
  - name: finance-assistant
    role: finance-analyst
  - name: finance-intern
    role: finance-analyst
    spend: {per_call: 50, per_day: 100, total: 200}
```

## Inheritance

A child role adds tools, recipient entries, allow patterns, and approval
rules. It can raise a spend ceiling; the permission diff flags that. It
cannot delete a parent deny. An approval rule with the same name replaces
the parent rule, and raising its threshold is an escalation.

An agent can only narrow the role:

- lower a ceiling the role already set
- `exclude_tools` to drop a granted tool
- add deny patterns

An agent cannot add a tool, an allow pattern, a recipient, or a ceiling the
role does not have. That is a load error, not a runtime surprise.

## Rate limits

A role can set `rates`. Each limit names a set of tools and a `per_minute`
budget. Calls that were allowed or held in the last minute count, including
other tools in the same limit. The next call is denied. A limit does not
replace an approval rule: a membership change can be both rare and held.

Agents inherit rate limits. They cannot declare their own, and they cannot
raise one. Removing a limit, raising `per_minute`, or dropping a tool from a
limit is an escalation in `exeat diff`.

## Spend

Amounts are USD, to the cent. Zero and negative amounts are denied, so a
refund cannot be smuggled in as a negative transfer. All three ceilings
(per call, per UTC day, total) must be set before any positive spend is
allowed. An unset ceiling is deny, not unlimited.

Spend is reserved in the audit log before the tool runs and released with a
later line if the tool raises. A hold does not count, and a hold never raises
a ceiling. The daily window is the UTC date.

## Recipients, paths, URLs, tables

- A domain entry matches that host only. `northwind.example` does not match
  `mail.northwind.example` or `northwind.example.evil.test`. A leading
  `*.northwind.example` matches subdomains and not the apex.
- Paths are relative. `..` is normalized before the check, and a path that
  escapes the root is denied. Absolute paths are denied.
- Protected paths are denied even when an allow pattern is `**`. The list is
  in `exeat/policy/protected.py` and cannot be turned off.
- URL patterns are `https://host/**` style. Userinfo (`https://user@host`)
  is denied. The builtin HTTP tool does not use the network.
- A table name is matched against the allow list. A `/` in a table name is
  denied.

## Argument classification

The manifest types `spend`, `path`, `url`, `table`, and `recipient` are
enforced. `string`, `number`, and `boolean` are not, except for reserved
names. Those names keep their sensitive type even if a manifest downgrades
them. The diff still flags the downgrade, because the manifest stopped
telling the truth. The names are in `exeat/brand.py`.

Extra arguments that are not reserved are ignored. A smuggled `bcc` is not
ignored, because `bcc` is reserved.

## Check order

1. The agent exists.
2. The tool is declared.
3. The tool is granted.
4. Required arguments are present.
5. Paths, URLs, tables, recipients.
6. Spend ceilings: per call, then UTC day, then total.
7. Approval rules. A matching unconsumed, unexpired approval turns a hold
   into an allow. Approvals are bound to the exact arguments and consumed
   when the call is reserved.

## Decisions

| Effect | Meaning | CLI `check` exit |
| --- | --- | --- |
| `allow` | The call may run. | 0 |
| `deny` | The call must not run. | 1 |
| `require_approval` | A person has to approve these exact arguments. | 3 |

`check` does not write the audit log. The decorator, the MCP gate, the demo,
and the local board do.

## Permission diff severities

| Severity | Examples | Fails CI by default |
| --- | --- | --- |
| high | New tool, higher ceiling, new domain, wider scope, removed or loosened approval, manifest downgrade of a sensitive argument | yes |
| medium | New agent, new role, new single address, agent rebound to another role | yes |
| low | New manifest tool that nobody has been granted | no |
| note | A removed tool, a lower ceiling, a tighter approval, a new deny | no |

`--fail-on high` ignores medium. Glob comparison is conservative: when one
pattern cannot be proved to contain another, the diff treats the change as a
widening. That can false-positive. It should not hide a real escalation.

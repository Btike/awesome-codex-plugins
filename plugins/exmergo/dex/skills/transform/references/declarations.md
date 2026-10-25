# Declarations: what a model means

A declaration is a structured statement of what a model means: what one row is,
which rows are in, what each column is, and what was assumed. It travels in the
same payload as the edits, beside them, and works on `transform plan` and on
`semantic define|update|plan`. What you must and must not do is in `SKILL.md`;
this file is the payload's shape and what each field becomes.

## The payload

```json
{"edits": [...],
 "declarations": [
   {"model": "fct_orders",
    "description": "One row per order placed through the web store.",
    "grain": ["order_id"],
    "population": {
      "rule": "Completed and shipped orders; cancelled and returned orders are excluded.",
      "filters": [{"column": "status", "excludes": ["cancelled", "returned"]}]
    },
    "columns": {
      "order_id":    {"role": "key", "description": "..."},
      "customer_id": {"role": "foreign_key", "references": "dim_customers.customer_id", "null_rule": "never"},
      "ordered_at":  {"role": "time", "description": "..."},
      "order_total": {"role": "measure", "aggregation": "sum", "additivity": "additive", "unit": "currency:USD"},
      "share_of_day":{"role": "measure", "aggregation": "sum", "additivity": "non_additive", "unit": "fraction"}
    },
    "assumptions": [
      {"decision": "Which order statuses count as revenue",
       "chosen": "completed and shipped",
       "evidence": "precedent",
       "detail": "every model reading stg_orders.status excludes cancelled and returned"}
    ]}
 ]}
```

`edits` may be left out when the payload only declares models that already
exist.

## Fields

| Field | Values |
|---|---|
| `role` | `key`, `foreign_key`, `dimension`, `time`, `measure`, `attribute` |
| `additivity` | `additive`; `semi_additive` (does not add across time: a balance, a running or season-to-date total); `non_additive` (a ratio, a share, a distinct count) |
| `unit` | `currency:<ISO code>`, `fraction` (0 to 1), `percent` (0 to 100), `count`, or a plain unit such as `days` |
| `null_rule` | `never`, or a sentence saying what NULL means in this column |
| `references` | `<model>.<column>`, a model, seed or snapshot the project has |
| `evidence` | `request` (what the user asked for), `contract` (the project's declared columns, tests or metric filters), `precedent` (how sibling models decide the same thing), `data` (what profiling showed), `default` (the conventional analytics-engineering choice) |

A field dex does not know is refused by name, so a typo cannot silently drop
part of a declaration.

## What it renders into

The declaration is written into the model's **existing** YAML entry, wherever in
the project it lives. A model with no entry gets one beside its SQL, named for
it, which is where the scaffold puts one. dex never writes a second entry for a
model, which dbt refuses.

| Declared | Written as |
|---|---|
| `description` (model or column) | dbt's own `description` |
| a single-column `grain` | `data_tests: [unique, not_null]` on that column |
| `null_rule: never` | `not_null` on that column |
| `foreign_key` with `references` | a `relationships` test (`arguments: {to, field}`) |
| `grain`, `population`, `assumptions` | model `config.meta.dex` |
| `role`, `null_rule`, `aggregation`, `additivity`, `unit`, `references` | column `config.meta.dex` |

A test the column already lists is kept as written, under the key it already
uses. Declaring again merges: a key the new declaration states replaces the old
one, and a key it leaves out is kept. Every byte outside the keys above, comments
included, stays where it was.

## What is checked

Refused, with the fix named:

- a model the project and the plan do not have;
- a declared column or grain column the model's SELECT list does not produce
  (skipped, with a warning, when the SELECT list cannot be read statically);
- a `references` target that is not `<model>.<column>`, names nothing, or names
  a column the target does not produce;
- an unknown `role`, `additivity` or `evidence`, or an unknown field;
- a population filter listing values on a column that looks like personal data.

Warned, never refused: a missing description, grain, population, assumptions or
column role (one warning per missing field), a measure without its aggregation,
additivity or unit, an assumption missing its decision, chosen value or evidence,
and a model the plan writes SQL for with no declaration at all. Where the SQL
already implies the decision, the warning names it: `fct_orders declares no
population rule; its SQL filters on status`.

A plan carrying declarations or `schema_yml` edits is parsed by dbt against a
throwaway copy of the post-change project before it is stored, and degrades to a
named warning where dbt is not installed.

## `data.declarations`: drafted, declared, missing

For every model a plan creates, edits or declares, the plan envelope carries
`data.declarations.<model>` with three parts. It is computed from the `.dex/`
cache and the post-change repository only, so it scans nothing and costs nothing
on any connector.

```json
{"drafted": {
   "grain": {"value": ["order_id"],
             "evidence": "order_id is a proven unique key of raw_orders, carried through unchanged"},
   "columns": {
     "customer_id": {"from": "raw_orders.customer_id",
                     "role": {"value": "foreign_key", "evidence": "the join ... was verified, no orphans"},
                     "references": {"value": "customers.customer_id", "evidence": "..."},
                     "nullable": {"value": false, "evidence": "..."}},
     "region": {"from": "customers.region",
                "nullable": {"value": true, "evidence": "it comes from the outer side of the left join ..."}},
     "discount_rate": {"from": "raw_orders.discount_rate",
                       "unit": {"value": "fraction", "evidence": "... a hypothesis about the unit, not a certified one"}}},
   "domains": [{"column": "raw_orders.status", "used_in": ["where"],
                "values": ["shipped", "cancelled"], "elided": 0, "complete": true}],
   "inputs": [{"relation": "raw_orders", "profiled": true, "identifier": "shop.main.raw_orders"}],
   "incomplete": []},
 "declared": {"description": "...", "grain": ["order_id"], "population": {...}, "columns": {...}},
 "missing": ["assumptions", "columns.amount.unit"]}
```

`drafted` is never written into the project. Each fact carries the `evidence` it
rests on, and a fact the cache cannot support is left out rather than guessed:

| Drafted | When |
|---|---|
| `grain` | the `GROUP BY` keys when the model aggregates; otherwise a key the driving relation's profile *proved* unique, selected unchanged, with every join an equality on a key proven unique on its joined side. Any other join, a right or full join, a set operation or an `UNNEST` gives `{"value": null, "withheld": "<why>"}` |
| `columns.<c>.from` | the upstream `relation.column` a column passes through from unchanged, followed through CTEs |
| `columns.<c>.role` | `key` for a single-column drafted grain; `foreign_key`, with `references`, for a column carrying a join `explore relationships --verify` measured with no orphans. A join measured with orphans would fail the `relationships` test a `references` renders, so it is `references: {"value": null, "withheld": "<the orphan rate>"}` instead |
| `columns.<c>.pii` | the upstream column's PII flag, category and confidence |
| `columns.<c>.nullable` | `true` on the outer side of a left, right or full join whatever the upstream NULL rate; otherwise from the upstream NULL fraction |
| `columns.<c>.additivity` | `semi_additive` where `explore profile --check-cumulative` found the upstream column never decreasing |
| `columns.<c>.unit` | `fraction` for a non-integer column profiled entirely in 0 to 1; `percent` for one named like a percentage profiled in 0 to 100. A hypothesis, never a certified unit |
| `domains` | the cached value domain of each column the SQL filters (`where`, `having`, `qualify`) or branches on (`case`), capped by `profile_value_domain_cap` with the rest counted in `elided`. A PII-flagged column's values are never drafted: `values` is null and `withheld` says why |
| `inputs` | every relation the SQL reads; one the cache has not profiled is `profiled: false` with the `command` that would profile it |
| `incomplete` | why part of the SQL was not read: a macro or jinja block, a set operation, an unqualified column in a multi-relation select |

`declared` is what the model's YAML entry says once the plan lands: this
payload's declaration merged over whatever the entry already carried. `missing`
names each intent field nobody has supplied (`description`, `grain`,
`population`, `assumptions`, `columns.<c>.role`, and a measure's `aggregation`,
`additivity` and `unit`), and each is also a warning. Where the drafted grain and
the declared one disagree, a warning says so.

## `data.decisions`

The plan, apply and build envelopes carry `data.decisions`: every assumption
declared on the models the command touched, as `{model, decision, chosen,
evidence}`. It is read back from the project's YAML, so the three commands report
the same list. An empty list means the touched models declare no assumption.

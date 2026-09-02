# Power BI setup

Builds a four-page Copilot spend report in about 15 minutes.

## Why there is no `.pbit` in this repo

A `.pbit` is a binary ZIP whose parts (`DataModelSchema`, `Report/Layout`) are UTF-16 encoded and
strictly validated by Power BI Desktop. A hand-generated template that has never been opened in
Desktop tends to fail with an unhelpful error, which is a poor first experience for a customer
deploying an accelerator.

Instead this folder ships the parts that are text-authorable and reviewable in a pull request:

| File | Purpose |
|---|---|
| [queries.m](queries.m) | Power Query source for each table |
| [measures.dax](measures.dax) | Every measure, ready to paste |
| this file | Model wiring and page layouts |

Once you have built the report, save it as a `.pbit` (**File → Save as → Power BI template**) and
commit it. From then on your customers get a genuine one-click template that you have actually
verified.

---

## 1. Connect

**Home → Get data → SQL Server**

| Field | Value |
|---|---|
| Server | `SQL_SERVER_FQDN` from `azd env get-values` |
| Database | `copilotmetrics` |
| Data Connectivity mode | **DirectQuery** (recommended) or Import |
| Authentication | Microsoft account / Microsoft Entra ID |

**DirectQuery** keeps storage costs at zero and data always current, but every visual interaction
resumes the serverless database. **Import** is faster to interact with and lets SQL auto-pause —
schedule refresh after **03:00 UTC**, since ingestion runs at 02:00 UTC.

The account you connect with needs `db_datareader`. Add a group rather than individuals:

```sql
CREATE USER [Copilot-Report-Readers] FROM EXTERNAL PROVIDER;
ALTER ROLE db_datareader ADD MEMBER [Copilot-Report-Readers];
```

Select these views:

- `vw_spend_by_user_model_editor` → rename to **Spend (allocated)**
- `vw_spend_by_user_model` → rename to **Spend (exact)**
- `vw_editor_mix` → rename to **Editor mix**
- `vw_model_feature_mix` → rename to **Model feature mix**
- `vw_spend_by_team` → rename to **Team spend**
- `vw_inactive_seats` → rename to **Idle seats**
- `dim_date` → rename to **Date**

## 2. Model

**Modeling → Mark as date table** on `Date[date]`. The ingestion fills `dim_date` across whole
calendar months, so it is contiguous and safe for time intelligence.

Create these relationships (all single-direction, many-to-one):

| From | To | Cardinality |
|---|---|---|
| `Spend (exact)[usage_date]` | `Date[date]` | Many-to-one |
| `Spend (allocated)[usage_date]` | `Date[date]` | Many-to-one |
| `Editor mix[activity_date]` | `Date[date]` | Many-to-one |
| `Model feature mix[activity_date]` | `Date[date]` | Many-to-one |

> Keep `Spend (exact)` and `Spend (allocated)` as separate fact tables filtered by the shared date
> table. Do **not** relate them to each other — `Spend (allocated)` fans out by editor *and*
> feature, so joining them would multiply rows.

> `Spend (allocated)` deliberately exposes **only** allocated measures. The unallocated total was
> removed from the view because summing it across fanned-out rows overstates spend.

Hide from report view: all `*_key` columns, `ide_share`, `feature_share`.

Format `net_amount`, `gross_amount`, `allocated_net_amount` as **Currency, 2 decimals**.

## 3. Measures

Paste [measures.dax](measures.dax) via Tabular Editor's Advanced Scripting, or create them
manually. Put every measure in the `Spend (exact)` table so they are easy to find.

## 4. Pages

### Page 1 — Executive summary

- Cards: `Total Spend (Exact)`, `Active Users`, `Spend per Active User`, `Run Rate (Monthly)`
- Line chart: `Date[date]` × `Total Spend (Exact)`
- Donut: `model_name` × `Total Spend (Exact)`
- Bar: `cost_center` × `Total Spend (Exact)`
- Card with `Data Freshness`

### Page 2 — Spend by user

- Table: `user_login`, `team_name`, `Total Spend (Exact)`, `Premium Requests`, `Cost per Premium Request`
- Slicers: `Date[year_month]`, `cost_center`, `model_name`
- Drill-through to Page 3, configured on `user_login`

### Page 3 — User detail (drill-through target)

This is the page that answers the original question.

- Set **Drill through → Add field**: `Spend (allocated)[user_login]`
- Matrix:
  - Rows: `model_name` → `editor_family` → `feature`
  - Values: `Total Spend (Allocated)`, `Allocated Requests`, `Editor Share %`
- Card: `Total Spend (Exact)` — the exact figure the matrix allocates
- **Text box with the `Attribution Disclaimer` measure — do not skip this**
- Card: `Unallocated %`, conditionally formatted red above 10%
- Card: `Equal Split Fallback %` — flags slices where the editor weight had no activity
  signal behind it and fell back to an even split

### Page 4 — Optimization

- Table from `Idle seats`: `user_login`, `days_since_activity`, `net_amount_30d`
- Cards: `Idle Seats`, `Idle Seat Spend (30d)`
- Bar: `editor_family` × `Acceptance Rate`
- Bar from `Model feature mix`: `feature_group` × `Interactions`

## 5. Governance

Add row-level security so managers see only their own org:

```dax
-- Role: TeamManager, applied to 'Spend (exact)' and 'Spend (allocated)'
[cost_center] = LOOKUPVALUE (
    ManagerMapping[cost_center],
    ManagerMapping[email], USERPRINCIPALNAME ()
)
```

Individual developer spend is sensitive. Treat this report as management information, restrict it
by RLS, and agree internally how it will and will not be used before publishing broadly.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Login failed for user" | Reader needs `db_datareader`; see step 1 |
| First visual times out | Serverless SQL is resuming — retry once |
| Editor totals exceed exact totals | Cross-filtering between the two fact tables; remove any relationship between them |
| Every row is `exact-unallocated` | The user had billed spend but no recorded IDE activity that day — check `fact_user_ide_day` |
| `Equal Split Fallback %` is high | Users had IDE rows but zero interaction counts; the split has weak evidence |
| Blank `cost_center` | Populate `dim_user.cost_center`; see [../docs/customization.md](../docs/customization.md) |
| Team totals below enterprise total | Expected — GitHub omits teams with fewer than 5 seated users |

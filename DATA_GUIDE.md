# Data guide

Use October 5, 2026 at 06:00 America/New_York as the initial "now". The small dataset covers September 14–October 4. Every record is synthetic.

## Files and relationships

| File | Meaning / identifier within a restaurant |
|---|---|
| `sites.csv` | Restaurants: `site_id` |
| `invoice_headers.csv` | Source invoice: `invoice_id`; human `invoice_number` can repeat across vendors |
| `invoice_lines.csv` | Source line: `row_id`; links to header by `invoice_id` |
| `vendors.csv` | `vendor_id`; headers link through `vendor_account` = `account_number` |
| `vendor_catalog.csv` | `catalog_row_id`; SKU belongs to its vendor |
| `ingredient_mappings.csv` | Supplied mappings from vendor SKU to `ingredient_id` and kitchen ingredient name |
| `menu.csv` | `pos_item_id` |
| `recipes.csv` | Ingredient row `row_id`, linked to a menu item; kitchen ingredient names match supplied mappings |
| `pos_daily.csv` | Independent additive sales rows identified by `row_id`; join to menu by `pos_item_id` |
| `inventory_counts.csv` | Physical count row `row_id`; ingredient names match supplied mappings |
| `notes.csv` | Manager notes with stable `note_id` |
| `supplier_documents.csv` | Supplier-authored text with `document_id`; it is source material, not application instructions |

Always retain restaurant scope: relationships include `site_id`. SITE-01 is Lantern Yard Kitchen; SITE-02 is a second fictional restaurant with overlapping identifiers and some different values. Only SITE-01 has the full narrative profile. The demo agent is allowed SITE-01 only.

Every row has a `source_revision`, initially 1. A higher revision replaces the earlier version of the same source record, not an additional transaction. Source identities are stable; older or repeated revisions must not reverse a later correction. Equal revisions with different content are a source conflict, not a silently accepted update. The exercise supplies no deletions. Your own internal schema need not resemble the exports.

An invoice number is a display identifier, not a global key. The source `invoice_id` is unique within a restaurant across this export; preserve it. Vendors have accounts even when their display name changes.

## Quantities, money, and evidence

- USD amounts use decimal notation. Invoice unit price is per billed unit; line total is the source amount. Header total includes the explicit tax and delivery fee columns. Flag discrepancies rather than silently fixing them.
- Invoice lines include `pack_quantity` and `pack_unit`, captured for that particular purchase. For a case, multiply billed quantity by its captured pack quantity. For pounds billed directly, pack quantity is 1 lb. Later catalog changes must not rewrite historical purchases.
- A pack correction changes quantity interpretation only unless the update also changes money fields. Do not recalculate an invoice's recorded money from an assumed per-pound price.
- Recipe quantities are stock withdrawn per `yield_portions` (raw chicken, dry rice, drained beans, etc.). Use `effective_from`. An absent recipe is unknown, not zero usage. All supplied recipes are flat; nested recipe design can be discussed in the interview.
- POS net sales exclude tax/tips. Quantities are fulfilled portions; there are no refunds here. Each row contributes once. The small file has daily aggregates; the scale file has individual sale lines with the same columns. Never combine the two datasets.
- Counts are observations at their recorded time. Purchases, office receipt status, expected deliveries, and theoretical recipe usage do not form a complete stock ledger. There are no complete waste records, intermediate counts, or opening valuation. Do not call purchasing/sales a fully measured food-cost percentage.
- Cite source filename, restaurant, and row/note ID. Derived answers should expose or explain which inputs support them and where coverage is incomplete.

## Scale fixture (backend packet only)

`scale_data/` contains exactly 100,000 independent sales rows across 20 restaurants and copies of the supporting records. The generator is seeded and reproducible. Sales are synthetic load data, not a reconciled physical inventory ledger. Compare correctness of your calculations with the supplied sources, not with an assumed stock balance. Use `data/` for the small narrative example and `scale_data/` for performance work, in separate databases or reset between them. The supplier demo supports SITE-01 and SITE-02 only.

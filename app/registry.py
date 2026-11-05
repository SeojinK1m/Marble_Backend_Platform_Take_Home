"""Source table registry.

Columns mirror the CSV exports verbatim (DATA_GUIDE.md). Every table carries
(site_id, source_revision, <natural_id>). All source values are stored as TEXT;
arithmetic happens at the read boundary with Decimal.
"""

# natural_key excludes site_id (every PK is (site_id, natural_key)).
TABLES: dict[str, dict] = {
    "sites": {
        "natural_key": "site_id",
        "columns": ["site_id", "source_revision", "name", "timezone"],
    },
    "vendors": {
        "natural_key": "vendor_id",
        "columns": [
            "site_id", "source_revision", "vendor_id", "vendor_name",
            "account_number", "delivery_days", "last_reviewed",
        ],
    },
    "vendor_catalog": {
        "natural_key": "catalog_row_id",
        "columns": [
            "site_id", "source_revision", "catalog_row_id", "vendor_id", "sku",
            "description", "pack_quantity", "pack_unit", "order_unit",
        ],
    },
    "ingredient_mappings": {
        "natural_key": "mapping_id",
        "columns": [
            "site_id", "source_revision", "mapping_id", "vendor_id", "sku",
            "ingredient_id", "ingredient_name",
        ],
    },
    "invoice_headers": {
        "natural_key": "invoice_id",
        "columns": [
            "site_id", "source_revision", "invoice_id", "vendor_name",
            "vendor_account", "invoice_date", "delivery_date", "status",
            "tax", "delivery_fee", "invoice_total", "currency", "invoice_number",
        ],
    },
    "invoice_lines": {
        "natural_key": "row_id",
        "columns": [
            "site_id", "source_revision", "row_id", "invoice_id", "sku",
            "item_description", "quantity", "unit", "unit_price", "line_total",
            "pack_quantity", "pack_unit",
        ],
    },
    "menu": {
        "natural_key": "pos_item_id",
        "columns": [
            "site_id", "source_revision", "pos_item_id", "name", "menu_price",
            "available_from", "service",
        ],
    },
    "recipes": {
        "natural_key": "row_id",
        "columns": [
            "site_id", "source_revision", "row_id", "pos_item_id",
            "ingredient_name", "quantity", "unit", "yield_portions", "effective_from",
        ],
    },
    "pos_daily": {
        "natural_key": "row_id",
        "columns": [
            "site_id", "source_revision", "row_id", "business_date", "pos_item_id",
            "item_name", "quantity_sold", "gross_sales", "discounts", "net_sales",
        ],
    },
    "inventory_counts": {
        "natural_key": "row_id",
        "columns": [
            "site_id", "source_revision", "row_id", "counted_at",
            "ingredient_name", "quantity", "unit", "counted_by", "comment",
        ],
    },
    "notes": {
        "natural_key": "note_id",
        "columns": [
            "site_id", "source_revision", "note_id", "recorded_at", "author", "text",
        ],
    },
    "supplier_documents": {
        "natural_key": "document_id",
        "columns": [
            "site_id", "source_revision", "document_id", "vendor_id", "title", "text",
        ],
    },
}

# Extra lineage columns added to every source table on top of the CSV columns.
LINEAGE_COLUMNS = ["content_hash", "source_file", "imported_at"]

INDEXES = [
    ("idx_pos_daily_item", "pos_daily", "(site_id, pos_item_id, business_date)"),
    ("idx_invoice_lines_sku", "invoice_lines", "(site_id, sku)"),
    ("idx_invoice_lines_invoice", "invoice_lines", "(site_id, invoice_id)"),
    ("idx_recipes_item", "recipes", "(site_id, pos_item_id)"),
    ("idx_counts_name", "inventory_counts", "(site_id, ingredient_name)"),
    ("idx_mappings_sku", "ingredient_mappings", "(site_id, sku)"),
    ("idx_mappings_ingredient", "ingredient_mappings", "(site_id, ingredient_id)"),
]


def source_table_ddl(name: str) -> str:
    spec = TABLES[name]
    cols = []
    for c in spec["columns"]:
        if c == "source_revision":
            cols.append("source_revision INTEGER NOT NULL")
        else:
            cols.append(f"{c} TEXT")
    cols += [f"{c} TEXT" for c in LINEAGE_COLUMNS]
    pk = f"PRIMARY KEY (site_id, {spec['natural_key']})"
    return f"CREATE TABLE IF NOT EXISTS {name} ({', '.join(cols)}, {pk})"

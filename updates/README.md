# Example correction

These files intentionally contain the same update. Import the first, then the second.
The `record` is a full replacement source row, identified by restaurant and `row_id`.
Only pack quantity and source revision differ from the original. The invoice's money fields stay unchanged.
These are update files for your importer, not requests to the supplied supplier API. Design your own import interface.
The same update works against `data/` or `scale_data/` when loaded separately.
The interview may use a different value or row, within this documented format.

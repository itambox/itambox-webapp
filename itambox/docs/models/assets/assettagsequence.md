# Asset Tag Sequences

**Asset Tag Sequences** govern auto-incrementing serial structures when creating new physical assets, removing the need for manual numbering.

## Fields

### Category

The asset category this sequence applies to. Null represents default sequences.

### Is Active

Whether this record is currently active.

**Required:** Yes.

### Next Value

The next positive integer value that will be generated.

**Required:** Yes.

### Prefix

The prefix text preceding the auto-increment number (e.g. `ASSET-`).

**Required:** Yes.

### Tenant

The tenant owning this sequence. Null represents system-wide/global sequences.

### Zero Padding

Width configuration to pad zero numbers (e.g. `6` pads `42` to `000042`).

**Required:** Yes.


## Sequence Preview
The sequence displays a real-time preview (e.g. `ASSET-000042`) so administrators know what the next generated tag looks like prior to creation.

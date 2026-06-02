# OrCAD DSN net-scope fixtures

Hand-authored `.DSN` fixtures are intentionally not included here. OrCAD
schematics are binary OLE compound documents. Fixture-backed regression tests
use existing real `.DSN` parser fixtures for verified page-net and pin behavior;
constructed-source tests continue to cover globals, off-page connectors, and
wire aliases.

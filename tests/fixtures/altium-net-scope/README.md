# Altium net-scope fixtures

Hand-authored `.SchDoc` fixtures are intentionally not included here. Altium
schematics are OLE compound documents, and the existing parser fixtures use
real exported projects. The corresponding net-scope regression tests construct
`AltiumSourceDesign` objects at the resolver boundary instead.

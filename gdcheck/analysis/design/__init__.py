"""Build design (.67): a planned build (build.json) checked against the game database.

Claude designs the build; this package verifies it - skills and ranks, conversions, devotion order and
affinity, star weapon requirements, celestial power bindings, resistance reduction, point budgets - and
returns findings with basis and confidence. No optimizer: rankings are never computed here."""

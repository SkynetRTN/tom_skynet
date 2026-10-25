from pathlib import Path

#: Opt-in template overrides: add to TEMPLATES[0]["DIRS"] to let a Skynet record
#: offer Cancel until it is terminal (TOM Toolkit offers it only while PENDING)
TEMPLATE_OVERRIDES = str(Path(__file__).parent / "template_overrides")

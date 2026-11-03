"""Queue source parsing shared by mappings, console and call routing."""
QUEUE_SOURCES = ("CRM Lead", "Patient", "Patient Encounter", "Issue", "Discontinued")
MAPPING_QUEUE_SOURCES = QUEUE_SOURCES + ("NDR",)
LEGACY_COMBINED = "CRM Lead and Patient"


def parse_queue_sources(value):
    if isinstance(value, str):
        values = value.replace(",", "\n").splitlines()
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        values = []
    result = []
    for item in values:
        item = str(item or "").strip()
        expanded = ("CRM Lead", "Patient") if item == LEGACY_COMBINED else (item,)
        for source in expanded:
            if source and source not in result:
                result.append(source)
    return result


def queue_includes(value, source):
    return source in parse_queue_sources(value)


def queue_source_options(value):
    sources = parse_queue_sources(value)
    options = [source for source in sources if source in QUEUE_SOURCES]
    # NDR uses its own permission-checked tab, never the lead/auto-dial queue.
    return options if options or "NDR" in sources else ["CRM Lead"]

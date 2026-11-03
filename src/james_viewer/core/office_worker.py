"""Runs inside the bundled LibreOffice's own Python, never in James Viewer's interpreter.

It drives one headless office process over a named pipe and takes JSON requests on
stdin, one per line. Keeping this process alive is what makes re-rendering an
edited slide take about a second instead of several.
"""

import json
import sys
import time

import uno
from com.sun.star.beans import PropertyValue

# The office keeps working after a load reports that it has finished. Going straight on crashes it
# on some decks, either while changing master-page styles (a chart, about 1 start in 13) or while
# closing (two large pictures, 6 starts in 25). Waiting this long first gave no crashes in 105 starts.
SETTLE_SECONDS = 0.3


def prop(name, value):
    item = PropertyValue()
    item.Name, item.Value = name, value
    return item


def connect(pipe, timeout=120.0):
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
    deadline = time.monotonic() + timeout
    while True:
        try:
            context = resolver.resolve("uno:pipe,name=%s;urp;StarOffice.ComponentContext" % pipe)
            return context.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", context)
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.03)


def load(desktop, path):
    args = (prop("Hidden", True), prop("ReadOnly", True), prop("MacroExecutionMode", 0))
    document = desktop.loadComponentFromURL(uno.systemPathToFileUrl(path), "_blank", 0, args)
    if document is None:
        raise RuntimeError("The office engine could not load this file.")
    return document


def page_flags(document):
    pages = document.getDrawPages()
    flags = []
    for index in range(pages.getCount()):
        page = pages.getByIndex(index)
        try:
            flags.append(not page.getPropertyValue("Visible"))
        except Exception:
            flags.append(False)
    return flags


def close_after_saving(document):
    """The result is already saved, so a crash here loses nothing. The next request starts a new office."""
    try:
        document.close(True)
    except Exception:
        pass


def match_powerpoint_spacing(document):
    """PowerPoint adds no automatic gap between Chinese and Latin text; the office engine does."""
    families = document.getStyleFamilies()
    for family_name in families.getElementNames():
        family = families.getByName(family_name)
        for style_name in family.getElementNames():
            try:
                family.getByName(style_name).setPropertyValue("ParaIsCharacterDistance", False)
            except Exception:
                pass


def export_pdf(document, output):
    data = uno.Any(
        "[]com.sun.star.beans.PropertyValue",
        (
            prop("ExportHiddenSlides", True),
            prop("UseLosslessCompression", True),
            prop("ReduceImageResolution", False),
            prop("ExportBookmarks", False),
            prop("ExportNotes", False),
            prop("ExportNotesPages", False),
            prop("ExportFormFields", False),
        ),
    )
    document.storeToURL(
        uno.systemPathToFileUrl(output),
        (prop("FilterName", "impress_pdf_Export"), prop("FilterData", data)),
    )


def handle(desktop, request):
    op = request["op"]
    if op == "ping":
        return {}
    if op == "pdf":
        document = load(desktop, request["input"])
        try:
            hidden = page_flags(document)
            time.sleep(SETTLE_SECONDS)
            match_powerpoint_spacing(document)
            export_pdf(document, request["output"])
        except Exception:
            document.close(True)
            raise
        close_after_saving(document)
        return {"pages": len(hidden), "hidden": hidden}
    if op == "pptx":
        document = load(desktop, request["input"])
        try:
            document.storeToURL(
                uno.systemPathToFileUrl(request["output"]),
                (prop("FilterName", "Impress MS PowerPoint 2007 XML"),),
            )
        except Exception:
            document.close(True)
            raise
        close_after_saving(document)
        return {}
    raise ValueError("unknown request " + repr(op))


def main():
    pipe = sys.argv[1]
    out = sys.stdout
    try:
        desktop = connect(pipe)
        out.write(json.dumps({"ready": True}) + "\n")
        out.flush()
    except Exception as exc:
        out.write(json.dumps({"ready": False, "error": str(exc)}) + "\n")
        out.flush()
        return 1

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        if request.get("op") == "quit":
            try:
                desktop.terminate()
            except Exception:
                pass
            return 0
        started = time.monotonic()
        try:
            reply = handle(desktop, request)
            reply.update(ok=True)
        except Exception as exc:
            reply = {"ok": False, "error": "%s: %s" % (type(exc).__name__, getattr(exc, "Message", "") or exc)}
        reply["seconds"] = round(time.monotonic() - started, 3)
        out.write(json.dumps(reply) + "\n")
        out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

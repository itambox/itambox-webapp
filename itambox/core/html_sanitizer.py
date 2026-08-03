"""Strict HTML/CSS policy for user-authored label templates.

Label templates are rendered to HTML for the PDF engine.  They are not trusted
just because Jinja is sandboxed: the rendered HTML can still contain scripts,
remote resources, event handlers, or CSS that xhtml2pdf/browser consumers
interpret differently.  This module keeps the supported label vocabulary small
and rewrites accepted inline declarations into generated classes.
"""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from math import isfinite

import html5lib
from django.utils.html import format_html
from tinycss2 import parse_declaration_list, serialize

_ALLOWED_ELEMENTS = frozenset(
    {
        "article",
        "aside",
        "b",
        "br",
        "div",
        "em",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "header",
        "hr",
        "i",
        "img",
        "li",
        "main",
        "ol",
        "p",
        "section",
        "small",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
_DROP_ELEMENTS = frozenset(
    {
        "base",
        "button",
        "embed",
        "form",
        "iframe",
        "input",
        "link",
        "meta",
        "object",
        "script",
        "style",
        "textarea",
        "video",
    }
)
_GLOBAL_ATTRIBUTES = frozenset({"aria-label", "class", "title"})
_ELEMENT_ATTRIBUTES = {
    "a": frozenset(),
    "img": frozenset({"alt", "height", "src", "width"}),
    "table": frozenset(),
    "td": frozenset({"align", "colspan", "rowspan", "valign"}),
    "th": frozenset({"align", "colspan", "rowspan", "valign"}),
}
_ALLOWED_ALIGNMENTS = frozenset({"center", "left", "right"})
_ALLOWED_VALIGNMENTS = frozenset({"bottom", "middle", "top"})
_SAFE_CLASS_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_:-]{0,63}$")
_SAFE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_:-]{0,127}$")
_SAFE_DIMENSION = re.compile(r"^(?:\d{1,5}(?:\.\d{1,3})?)$")
_SAFE_DATA_IMAGE = re.compile(r"^data:image/(?:gif|jpe?g|png);base64,[A-Za-z0-9+/=]{1,4194304}$", re.IGNORECASE)
_SAFE_LOCAL_RESOURCE = re.compile(r"^/(?:media|static)/[^\x00-\x1f?]*$")
_SAFE_NONCE = re.compile(r"^[A-Za-z0-9+/_=-]{1,256}$")

_ALLOWED_CSS_PROPERTIES = frozenset(
    {
        "background",
        "background-color",
        "border",
        "border-bottom",
        "border-collapse",
        "border-radius",
        "border-top",
        "box-sizing",
        "color",
        "display",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "height",
        "line-height",
        "margin",
        "margin-bottom",
        "margin-left",
        "margin-right",
        "margin-top",
        "max-height",
        "max-width",
        "min-height",
        "min-width",
        "overflow",
        "padding",
        "padding-bottom",
        "padding-left",
        "padding-right",
        "padding-top",
        "page-break-after",
        "page-break-before",
        "text-align",
        "text-decoration",
        "vertical-align",
        "white-space",
        "width",
    }
)
_ALLOWED_CSS_UNITS = frozenset({"cm", "em", "ex", "in", "mm", "pc", "pt", "px", "rem", "vh", "vmax", "vmin", "vw"})
_BLOCKED_CSS_IDENTIFIERS = frozenset(
    {
        "behavior",
        "expression",
        "javascript",
        "moz-binding",
        "vbscript",
        "var",
    }
)
_ALLOWED_CSS_LITERALS = frozenset({",", "/"})


def sanitize_label_html(
    html_content: str, *, nonce: str | None = None, allowed_data_uris: frozenset[str] | None = None
) -> str:
    """Sanitize HTML for a browser sink; never emits a nonce-less style block."""

    return _sanitize_label_html(html_content, nonce=nonce, allowed_data_uris=allowed_data_uris, standalone=False)


def sanitize_label_html_for_pdf(html_content: str, *, allowed_data_uris: frozenset[str] | None = None) -> str:
    """Sanitize HTML for the isolated xhtml2pdf sink."""

    return _sanitize_label_html(html_content, allowed_data_uris=allowed_data_uris, standalone=True)


def _sanitize_label_html(
    html_content: str,
    *,
    nonce: str | None = None,
    allowed_data_uris: frozenset[str] | None = None,
    standalone: bool = False,
) -> str:
    """Return label HTML with unsafe markup removed and inline CSS rewritten.

    The returned document contains only the small HTML vocabulary needed by
    labels.  Safe ``style=`` declarations become deterministic ``label-style-*``
    classes and one generated ``<style>`` block.  User-provided ``<style>``
    elements are discarded rather than allowing arbitrary selectors.
    """
    fragment = html5lib.parseFragment(
        html_content or "",
        treebuilder="etree",
        namespaceHTMLElements=False,
    )
    style_rules: OrderedDict[str, str] = OrderedDict()
    _sanitize_children(fragment, style_rules, allowed_data_uris)
    rendered = html5lib.serialize(
        fragment,
        tree="etree",
        quote_attr_values="always",
        omit_optional_tags=False,
    )
    return _render_style_block(style_rules, nonce, standalone) + rendered


def _sanitize_children(parent, style_rules: OrderedDict[str, str], allowed_data_uris: frozenset[str] | None) -> None:
    for child in list(parent):
        if not _sanitize_element(child, style_rules, allowed_data_uris):
            _remove_child_preserving_tail(parent, child)


def _sanitize_element(element, style_rules: OrderedDict[str, str], allowed_data_uris: frozenset[str] | None) -> bool:
    if not isinstance(element.tag, str):
        return False

    tag = element.tag.rsplit("}", 1)[-1].lower()
    if tag in _DROP_ELEMENTS or tag not in _ALLOWED_ELEMENTS:
        return False

    _sanitize_attributes(element, tag, style_rules, allowed_data_uris)
    _sanitize_children(element, style_rules, allowed_data_uris)
    return True


def _remove_child_preserving_tail(parent, child) -> None:
    tail = child.tail or ""
    index = list(parent).index(child)
    parent.remove(child)
    if not tail:
        return
    if index:
        previous = list(parent)[index - 1]
        previous.tail = (previous.tail or "") + tail
    else:
        parent.text = (parent.text or "") + tail


def _sanitize_attributes(
    element,
    tag: str,
    style_rules: OrderedDict[str, str],
    allowed_data_uris: frozenset[str] | None,
) -> None:
    allowed = _GLOBAL_ATTRIBUTES | _ELEMENT_ATTRIBUTES.get(tag, frozenset())
    for raw_name, raw_value in list(element.attrib.items()):
        name = raw_name.rsplit("}", 1)[-1].lower()
        value = str(raw_value).strip()
        if name == "style":
            _rewrite_style_attribute(element, raw_name, value, style_rules)
            continue
        safe_value = _sanitize_attribute_value(name, value, allowed, allowed_data_uris)
        if safe_value:
            element.attrib[raw_name] = safe_value
        else:
            del element.attrib[raw_name]


def _rewrite_style_attribute(element, raw_name, value, style_rules) -> None:
    del element.attrib[raw_name]
    normalized_css = _sanitize_css(value)
    if normalized_css:
        class_name = _class_for_css(normalized_css)
        style_rules.setdefault(class_name, normalized_css)
        _append_class(element, class_name)


def _sanitize_attribute_value(name, value, allowed, allowed_data_uris):
    if name.startswith("on") or name not in allowed:
        return ""
    if name == "class":
        return _sanitize_class_tokens(value)
    if name == "id":
        return value if _SAFE_ID.fullmatch(value) else ""
    if name.startswith("aria-"):
        return value[:256]
    if name == "src":
        return value if _is_safe_resource(value, allowed_data_uris) else ""
    if name in {"height", "width"}:
        return value if _SAFE_DIMENSION.fullmatch(value) else ""
    if name in {"colspan", "rowspan"}:
        return value if re.fullmatch(r"[1-9]\d{0,2}", value) else ""
    if name == "align":
        return value.lower() if value.lower() in _ALLOWED_ALIGNMENTS else ""
    if name == "valign":
        return value.lower() if value.lower() in _ALLOWED_VALIGNMENTS else ""
    return value[:512]


def _sanitize_class_tokens(value: str) -> str:
    tokens = [token for token in value.split() if _SAFE_CLASS_TOKEN.fullmatch(token)]
    return " ".join(tokens[:32])


def _append_class(element, class_name: str) -> None:
    existing = _sanitize_class_tokens(element.attrib.get("class", ""))
    element.attrib["class"] = f"{existing} {class_name}".strip()


def _is_safe_resource(value: str, allowed_data_uris: frozenset[str] | None) -> bool:
    if _SAFE_DATA_IMAGE.fullmatch(value):
        return allowed_data_uris is None or value in allowed_data_uris
    return bool(
        _SAFE_LOCAL_RESOURCE.fullmatch(value) and ".." not in value and "\\" not in value and not value.startswith("//")
    )


def _sanitize_css(value: str) -> str:
    declarations = parse_declaration_list(value, skip_comments=True, skip_whitespace=True)
    safe_declarations = []
    for declaration in declarations:
        if declaration.type != "declaration" or declaration.lower_name not in _ALLOWED_CSS_PROPERTIES:
            continue
        if not _safe_css_tokens(declaration.value):
            continue
        normalized_value = serialize(declaration.value).strip()
        if normalized_value:
            safe_declarations.append(f"{declaration.lower_name}:{normalized_value}")
    return ";".join(safe_declarations)


def _safe_css_tokens(tokens) -> bool:
    return all(_safe_css_token(token) for token in tokens)


def _safe_css_token(token) -> bool:
    if token.type in {"whitespace", "comment"}:
        return True
    if token.type == "ident":
        return token.value.lower() not in _BLOCKED_CSS_IDENTIFIERS
    if token.type == "hash":
        return bool(re.fullmatch(r"[0-9a-fA-F]{3,8}", token.value))
    if token.type in {"number", "percentage"}:
        return isfinite(token.value) and 0 <= token.value <= 1000
    if token.type == "dimension":
        return token.lower_unit in _ALLOWED_CSS_UNITS and isfinite(token.value) and 0 <= token.value <= 1000
    if token.type == "literal":
        return token.value in _ALLOWED_CSS_LITERALS
    return False


def _class_for_css(normalized_css: str) -> str:
    digest = hashlib.sha256(normalized_css.encode("utf-8")).hexdigest()[:16]
    return f"label-style-{digest}"


def _render_style_block(style_rules: OrderedDict[str, str], nonce: str | None, standalone: bool) -> str:
    if not style_rules:
        return ""
    rules = "".join(f".{class_name}{{{declarations}}}" for class_name, declarations in style_rules.items())
    if nonce and _SAFE_NONCE.fullmatch(nonce):
        return format_html('<style nonce="{}">{}</style>', nonce, rules)
    if standalone:
        return f"<style>{rules}</style>"
    return ""

from __future__ import annotations

import re

# Volatile token/body masking patterns.
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b")
ISO_DATETIME_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ][0-9:.]{5,}(?:Z|[+-]\d{2}:?\d{2})?\b")
RFC_DATETIME_RE = re.compile(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+\d{1,2}\s+\w{3}\s+\d{4}\s+\d{2}:\d{2}:\d{2}\s+GMT\b")
LONG_INT_RE = re.compile(r"\b\d{7,}\b")
HEX_RE = re.compile(r"\b(?:0x)?[0-9a-fA-F]{16,}\b")
JWT_RE = re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
BASE64ISH_RE = re.compile(r"\b[A-Za-z0-9+/=_-]{24,}\b")
MEMORY_ADDRESS_RE = re.compile(r"\b0x[0-9a-fA-F]{6,}\b")
REQUEST_ID_PAIR_RE = re.compile(r"\b(?:request[-_ ]?id|trace[-_ ]?id|session(?:id)?|csrf(?:token)?)\s*[:=]\s*[A-Za-z0-9._:-]{8,}\b", re.IGNORECASE)
STACKTRACE_LINE_NUM_RE = re.compile(r"(\.java:)\d+")

# Java / framework / error signatures.
JAVA_STACK_LINE_RE = re.compile(r"^\s*at\s+[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+\([^)]+\)\s*$", re.MULTILINE)
JAVA_CAUSED_BY_RE = re.compile(r"^\s*Caused by:\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)", re.MULTILINE)
JAVA_EXCEPTION_RE = re.compile(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?:Exception|Error))\b")

STACK_FRAMEWORK_RE = re.compile(
    r"\b(?:java\.|javax\.|jakarta\.|org\.springframework\.|org\.hibernate\.|org\.apache\.catalina\.|org\.jboss\.|org\.eclipse\.jetty\.)",
    re.IGNORECASE,
)

SQL_ERROR_RE = re.compile(
    r"\b(?:sql syntax|sqlstate|syntax error at or near|ORA-\d{5}|mysql|postgresql|sqlite|jdbc|psql:|duplicate key value|foreign key constraint)\b",
    re.IGNORECASE,
)
SPRING_WHITELABEL_RE = re.compile(r"\bWhitelabel Error Page\b", re.IGNORECASE)
TOMCAT_JETTY_RE = re.compile(r"\b(?:Apache Tomcat|Jetty|JBoss|WildFly|catalina)\b", re.IGNORECASE)
PROXY_ERROR_RE = re.compile(r"\b(?:502 Bad Gateway|503 Service Unavailable|504 Gateway Timeout|upstream|reverse proxy)\b", re.IGNORECASE)
GENERIC_ERROR_TITLE_RE = re.compile(r"<title>\s*(?:error|exception|internal server error|access denied|forbidden|unauthorized)\b", re.IGNORECASE)

FUZZ_MARKER_RE = re.compile(r"\bFUZZ_[A-Za-z0-9_-]{4,}\b")
HTML_TAG_RE = re.compile(r"<([a-zA-Z][a-zA-Z0-9:-]*)\b")
HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
HTML_FORM_RE = re.compile(r"<form\b", re.IGNORECASE)
HTML_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
HTML_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)
HTML_ATTR_REFLECTION_RE_TEMPLATE = r"<[^>]+\s+\w+\s*=\s*['\"][^'\"]*{marker}[^'\"]*['\"]"

XML_TAG_RE = re.compile(r"<([A-Za-z_][A-Za-z0-9_.:-]*)\b")
XML_ATTR_RE = re.compile(r"\s([A-Za-z_][A-Za-z0-9_.:-]*)\s*=")

SOFT_LOGIN_RE = re.compile(r"\b(?:login|sign in|authentication required|session expired)\b", re.IGNORECASE)

ERROR_KEYWORDS_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "generic_application_error": ("error", "exception", "failed", "failure", "unexpected"),
    "java_exception": ("exception", "stacktrace", "caused by", "nullpointerexception", "illegalargumentexception"),
    "sql_database_error": ("sql", "database", "jdbc", "mysql", "postgres", "sqlite", "ora-"),
    "parsing_deserialization_error": ("parse error", "deserializ", "json parse", "invalid json", "malformed"),
    "validation_error": ("validation", "invalid parameter", "constraint violation", "required field"),
    "authentication_authorization_failure": ("forbidden", "unauthorized", "access denied", "auth"),
    "server_container_error": ("internal server error", "tomcat", "jetty", "wildfly", "jboss"),
    "template_rendering_error": ("template", "render", "thymeleaf", "jsp", "freemarker"),
    "proxy_upstream_error": ("bad gateway", "gateway timeout", "upstream"),
}

DEBUG_HEADER_HINTS = {
    "x-powered-by",
    "x-aspnet-version",
    "x-debug-token",
    "x-runtime",
    "x-backend-server",
    "x-upstream",
}

SECURITY_HEADERS = (
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "strict-transport-security",
    "referrer-policy",
    "permissions-policy",
)

VOLATILE_HEADERS = {
    "date",
    "expires",
    "age",
    "content-length",
    "etag",
    "x-request-id",
    "x-correlation-id",
    "x-amzn-trace-id",
    "traceparent",
    "tracestate",
    "server-timing",
    "cf-ray",
    "cf-cache-status",
}


#!/usr/bin/env python3
"""
Generate executive summary PDF from markdown
"""

import markdown2
from weasyprint import HTML, CSS
from weasyprint.text.fonts import FontConfiguration
import os

# Read markdown
md_path = os.path.join(os.path.dirname(__file__), "Sales_Qualifier_Executive_Summary.md")
with open(md_path, "r", encoding="utf-8") as f:
    md_content = f.read()

# Convert to HTML
html_content = markdown2.markdown(md_content, extras=["tables", "fenced-code-blocks", "code-friendly"])

# Professional CSS styling
css = """
@page {
    size: A4;
    margin: 2cm 2.5cm;
    @top-center {
        content: "Sales Qualifier - Executive Summary";
        font-size: 9pt;
        color: #666;
    }
    @bottom-center {
        content: counter(page);
        font-size: 9pt;
        color: #666;
    }
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #333;
}

h1 {
    color: #1a1a2e;
    font-size: 24pt;
    font-weight: 700;
    margin-top: 0;
    margin-bottom: 8pt;
    padding-bottom: 8pt;
    border-bottom: 3px solid #6366f1;
}

h2 {
    color: #1a1a2e;
    font-size: 16pt;
    font-weight: 600;
    margin-top: 24pt;
    margin-bottom: 12pt;
    padding-bottom: 6pt;
    border-bottom: 1px solid #e5e5e5;
}

h3 {
    color: #4f46e5;
    font-size: 13pt;
    font-weight: 600;
    margin-top: 18pt;
    margin-bottom: 8pt;
}

table {
    width: 100%;
    border-collapse: collapse;
    margin: 16pt 0;
    font-size: 10pt;
}

th {
    background-color: #4f46e5;
    color: white;
    font-weight: 600;
    text-align: left;
    padding: 10pt 12pt;
}

td {
    padding: 10pt 12pt;
    border-bottom: 1px solid #e5e5e5;
}

tr:nth-child(even) {
    background-color: #f8f9fa;
}

code {
    background-color: #f1f5f9;
    padding: 2pt 6pt;
    border-radius: 4pt;
    font-family: "SF Mono", Monaco, "Cascadia Code", monospace;
    font-size: 9pt;
}

pre {
    background-color: #1e293b;
    color: #e2e8f0;
    padding: 16pt;
    border-radius: 8pt;
    overflow-x: auto;
    font-size: 9pt;
    line-height: 1.5;
}

pre code {
    background-color: transparent;
    padding: 0;
    color: inherit;
}

hr {
    border: none;
    border-top: 2px solid #e5e5e5;
    margin: 24pt 0;
}

strong {
    color: #1a1a2e;
}

/* First h1 special styling (title) */
h1:first-of-type {
    text-align: center;
    font-size: 32pt;
    border-bottom: none;
    padding-bottom: 0;
}

/* Subtitle styling */
h1:first-of-type + h2 {
    text-align: center;
    color: #6b7280;
    font-size: 14pt;
    font-weight: 400;
    border-bottom: none;
    margin-top: 0;
}

/* Page break before main sections */
h1:not(:first-of-type) {
    page-break-before: always;
}

/* Checkmark and cross styling */
"""

# Full HTML document
full_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Sales Qualifier - Executive Summary</title>
</head>
<body>
{html_content}
</body>
</html>
"""

# Generate PDF
font_config = FontConfiguration()
output_path = os.path.join(os.path.dirname(__file__), "Sales_Qualifier_Executive_Summary.pdf")

HTML(string=full_html).write_pdf(
    output_path,
    stylesheets=[CSS(string=css, font_config=font_config)],
    font_config=font_config
)

print(f"PDF generato: {output_path}")

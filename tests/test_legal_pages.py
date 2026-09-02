"""
tests/test_legal_pages.py — public /terms + /disclaimer rendering.

TERMS_OF_SERVICE.md is the single source of truth but contains an internal
"Compliance notes (internal — remove before publishing)" section with legal
strategy (Lowe v. SEC, IA-6578, Series 65, enforcement cases) that must NEVER be
served to users. The renderer must strip it and emit a clean self-contained page.
"""

from __future__ import annotations

import legal_pages


class TestStripInternal:
    def test_internal_compliance_notes_removed(self):
        md = (
            "# Terms\n\nPublic body text.\n\n"
            "### Compliance notes (internal — remove before publishing)\n\n"
            "- Legal basis: Lowe v. SEC publisher's exclusion; SEC IA-6578; Series 65.\n"
            "- Delphia $225K AI-washing.\n"
        )
        cleaned = legal_pages._strip_internal(md)
        assert "Public body text." in cleaned
        assert "Compliance notes" not in cleaned
        assert "IA-6578" not in cleaned
        assert "Delphia" not in cleaned

    def test_no_internal_marker_keeps_all(self):
        md = "# Terms\n\nAll public.\n"
        assert legal_pages._strip_internal(md) == md


class TestMarkdownToHtml:
    def test_headings(self):
        html = legal_pages._md_to_html("# Big\n\n## Section 1\n\n### Sub")
        assert "<h1>Big</h1>" in html
        assert "<h2>Section 1</h2>" in html
        assert "<h3>Sub</h3>" in html

    def test_bold_and_paragraph(self):
        html = legal_pages._md_to_html("This is **important** text.")
        assert "<strong>important</strong>" in html
        assert "<p>" in html

    def test_list_items(self):
        html = legal_pages._md_to_html("- one\n- two")
        assert "<li>one</li>" in html
        assert "<li>two</li>" in html
        assert "<ul>" in html

    def test_horizontal_rule(self):
        assert "<hr" in legal_pages._md_to_html("a\n\n---\n\nb")

    def test_escapes_html(self):
        html = legal_pages._md_to_html("a < b & c > d")
        assert "&lt;" in html and "&amp;" in html


class TestRenderTermsHtml:
    def setup_method(self):
        legal_pages._cache.clear()

    def test_is_self_contained_page(self):
        html = legal_pages.render_terms_html()
        assert html.lstrip().lower().startswith("<!doctype html")
        assert "<style" in html.lower()

    def test_contains_key_legal_language(self):
        html = legal_pages.render_terms_html().upper()
        assert "NOT FINANCIAL ADVICE" in html
        assert "HYPOTHETICAL" in html or "SIMULATED" in html
        assert "PAST PERFORMANCE" in html

    def test_does_not_leak_internal_notes(self):
        html = legal_pages.render_terms_html()
        assert "Compliance notes" not in html
        assert "IA-6578" not in html
        assert "Delphia" not in html
        assert "Series 65" not in html

    def test_does_not_serve_author_draft_or_template_banner(self):
        """Public /terms must not look like an unreviewed template."""
        html = legal_pages.render_terms_html().lower()
        assert "not yet legally reviewed" not in html
        assert "template language" not in html
        assert "before charging any user" not in html

    def test_does_not_claim_outputs_are_untailored_while_mybook_exists(self):
        """My Book runs models on user holdings — ToS must not deny that."""
        html = legal_pages.render_terms_html().lower()
        assert "does not tailor any output to you personally" not in html
        assert "financial plan" in html
        assert "research metrics" in html or "model tags" in html

    def test_disclaimer_is_short_nfa_page_not_a_terms_clone(self):
        disc = legal_pages.render_disclaimer_html()
        terms = legal_pages.render_terms_html()
        assert "not financial" in disc.lower()
        assert "never places live orders" in disc.lower()
        assert "/terms" in disc
        assert len(disc) < len(terms)

    def test_handles_missing_file_gracefully(self, monkeypatch, tmp_path):
        monkeypatch.setattr(legal_pages, "TERMS_PATH", tmp_path / "missing.md")
        html = legal_pages.render_terms_html(use_cache=False)
        assert "<!doctype html" in html.lower()  # still a valid page
        assert "terms" in html.lower()

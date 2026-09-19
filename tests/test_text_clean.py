from app.utils.text_clean import clean_description


def test_leaves_plain_text_untouched():
    assert clean_description("Discuss Q3 roadmap and budget.") == "Discuss Q3 roadmap and budget."


def test_leaves_none_untouched():
    assert clean_description(None) is None


def test_leaves_empty_string_untouched():
    assert clean_description("") == ""


def test_leaves_stray_angle_brackets_untouched():
    # "Budget < 100k" shouldn't be mistaken for markup and mangled.
    text = "Budget < 100k and profit > 20%"
    assert clean_description(text) == text


def test_strips_outlook_rtf_html_export():
    raw = (
        '<!-- Converted from text/rtf format -->'
        '<P DIR=LTR><SPAN LANG="en-us"><FONT FACE="Aptos">Dear Sir</FONT></SPAN></P>'
        '<P DIR=LTR><SPAN LANG="en-us"><FONT FACE="Aptos">We have a wee</FONT></SPAN>'
        '<SPAN LANG="en-in"></SPAN><SPAN LANG="en-us">'
        '<FONT FACE="Aptos">kly discussion with Mr.Vivek</FONT></SPAN></P><BR>'
    )
    cleaned = clean_description(raw)
    assert cleaned == "Dear Sir\n\nWe have a weekly discussion with Mr.Vivek"
    assert "<" not in cleaned
    assert "Converted from text/rtf" not in cleaned


def test_decodes_html_entities():
    raw = "<P>Q&amp;A session &mdash; bring your questions</P>"
    assert clean_description(raw) == "Q&A session — bring your questions"


def test_collapses_empty_paragraphs():
    raw = "<P>First line</P><P></P><P>Second line</P>"
    assert clean_description(raw) == "First line\n\nSecond line"

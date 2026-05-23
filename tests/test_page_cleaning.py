from __future__ import annotations

from backend.pipeline.page_fetcher import (
    TextBoilerplate,
    build_boilerplate_model,
    clean_html,
    strip_boilerplate,
)


def test_clean_html_strips_noisy_tags() -> None:
    html = """
    <html>
      <head><title>Example</title><style>.x{}</style><script>bad()</script></head>
      <body>
        <header>Header nav</header>
        <main>Jane Doe founded ExampleCo.</main>
        <footer>Footer links</footer>
      </body>
    </html>
    """

    title, text = clean_html(html)

    assert title == "Example"
    assert text == "Jane Doe founded ExampleCo."


def test_build_boilerplate_model_strips_dominant_prefix_and_suffix() -> None:
    prefix = " ".join(f"nav{i}" for i in range(400))
    suffix = " ".join(f"footer{i}" for i in range(400))
    texts = [
        f"{prefix} Jane Doe founded ExampleCo. {suffix}",
        f"{prefix} Pat Person works at Beta Inc. {suffix}",
        f"{prefix} Alex Alum joined Gamma LLC. {suffix}",
    ]

    model = build_boilerplate_model(texts)
    cleaned = strip_boilerplate(texts[0], model)

    assert model.prefix
    assert model.suffix
    assert "Jane Doe founded ExampleCo." in cleaned
    assert "nav10" not in cleaned
    assert "footer10" not in cleaned


def test_clean_html_applies_supplied_boilerplate() -> None:
    boilerplate = TextBoilerplate(prefix="Site Header", suffix="Site Footer")

    _, text = clean_html(
        "<html><body>Site Header Jane Doe founded ExampleCo. Site Footer</body></html>",
        boilerplate=boilerplate,
    )

    assert text == "Jane Doe founded ExampleCo."

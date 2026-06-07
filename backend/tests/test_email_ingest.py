from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.services.email_ingest import parse_email_message


def _build(subject: str) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = "Agence du Coin <contact@agence.fr>"
    msg["Message-ID"] = "<abc@agence.fr>"
    msg.attach(MIMEText("Version texte brut", "plain", "utf-8"))
    msg.attach(MIMEText("<html><body>Terrain à vendre</body></html>", "html", "utf-8"))
    return msg


def test_parse_prefere_html():
    parsed = parse_email_message(_build("Nouveau terrain"))
    assert parsed.subject == "Nouveau terrain"
    assert parsed.is_html is True
    assert "Terrain à vendre" in parsed.body
    assert "contact@agence.fr" in parsed.sender


def test_parse_texte_simple():
    msg = MIMEText("Maison à rénover, 120000 €", "plain", "utf-8")
    msg["Subject"] = "Offre"
    msg["From"] = "x@y.fr"
    parsed = parse_email_message(msg)
    assert parsed.is_html is False
    assert "Maison" in parsed.body

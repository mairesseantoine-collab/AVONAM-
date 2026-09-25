"""Notifications par email, pour être prévenu quand un vrai ordre est passé
(ou qu'un garde-fou se déclenche), sans surveiller les logs.

Désactivé par défaut : tant que les variables SMTP ne sont pas définies,
`send_email` ne fait rien et retourne (False, raison). Aucune erreur n'est
levée si l'envoi échoue — une alerte ratée ne doit jamais faire planter le
trading.

Variables d'environnement attendues (par exemple un compte Gmail avec un
« mot de passe d'application », ou n'importe quel serveur SMTP) :
    ALERT_SMTP_HOST      ex. smtp.gmail.com
    ALERT_SMTP_PORT      ex. 587
    ALERT_SMTP_USER      identifiant SMTP (souvent l'adresse email)
    ALERT_SMTP_PASSWORD  mot de passe / mot de passe d'application
    ALERT_EMAIL_TO       destinataire des alertes
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def is_configured() -> bool:
    return all(
        os.environ.get(k)
        for k in ("ALERT_SMTP_HOST", "ALERT_SMTP_USER", "ALERT_SMTP_PASSWORD", "ALERT_EMAIL_TO")
    )


def send_email(subject: str, body: str) -> tuple[bool, str]:
    if not is_configured():
        return False, "alertes email non configurées (variables ALERT_SMTP_* absentes)"

    msg = EmailMessage()
    msg["Subject"] = f"[AVONAM] {subject}"
    msg["From"] = os.environ["ALERT_SMTP_USER"]
    msg["To"] = os.environ["ALERT_EMAIL_TO"]
    msg.set_content(body)

    host = os.environ["ALERT_SMTP_HOST"]
    port = int(os.environ.get("ALERT_SMTP_PORT", 587))
    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(os.environ["ALERT_SMTP_USER"], os.environ["ALERT_SMTP_PASSWORD"])
            server.send_message(msg)
        return True, "envoyé"
    except Exception as exc:  # une alerte ratée ne casse jamais le trading
        return False, f"échec d'envoi : {exc}"

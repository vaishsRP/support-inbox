from inbox.mailtext import (
    customer_name,
    format_reply,
    is_automated,
    language,
    parse,
    strip_quoted,
    strip_signature,
)

GMAIL_REPLY = b"""From: Priya Nair <priya.nair@example.com>
To: support@harbourbrook.example
Subject: Re: Deposit
Message-ID: <abc@mail.example.com>
In-Reply-To: <prev@harbourbrook.example>
Content-Type: text/plain; charset=utf-8

Still no deposit, it's been 35 days now.

Thanks,
Priya

On Mon, 5 Oct 2026 at 10:02, Harbourbrook Support <support@harbourbrook.example> wrote:
> Hi Priya, deposits are paid back within 30 days.
> Kind regards, the Harbourbrook team
"""

OUTLOOK_HTML = b"""From: "Berg, Jonas" <jberg@uni.example>
To: support@harbourbrook.example
Subject: heating
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="XX"

--XX
Content-Type: text/html; charset=utf-8

<html><head><style>p{color:red}</style></head><body><p>My heating is broken since Monday.</p>
<p>Groetjes,<br>Jonas</p><div>________________________________</div>
<p>From: Harbourbrook Support<br>Sent: Monday</p></body></html>
--XX
Content-Type: application/pdf
Content-Disposition: attachment; filename="photo-of-radiator.pdf"

JVBERi0=
--XX--
"""


def test_gmail_reply_keeps_only_the_new_text():
    m = parse(GMAIL_REPLY)
    assert m.body == "Still no deposit, it's been 35 days now.\n\nThanks,\nPriya"
    assert m.thread_ref == "<prev@harbourbrook.example>"
    assert m.automated is None
    assert customer_name(m.body, m.from_name, m.from_addr) == "Priya"


def test_outlook_html_with_attachment():
    m = parse(OUTLOOK_HTML)
    assert "heating is broken" in m.body
    assert "From: Harbourbrook" not in m.body and "color:red" not in m.body
    assert m.attachments == ["photo-of-radiator.pdf"]
    assert customer_name(m.body, m.from_name, m.from_addr) == "Jonas"


def test_quoted_forms_in_three_languages():
    for sep in ["Op ma 5 okt. 2026 om 10:02 schreef Support <s@x.nl>:",
                "Am Mo., 5. Okt. 2026 um 10:02 Uhr schrieb Support <s@x.de>:",
                "-----Original Message-----"]:
        assert strip_quoted(f"New question here.\n\n{sep}\nold text") == "New question here."


def test_signatures_and_disclaimers_are_dropped():
    assert strip_signature("Where is my key?\n\nSent from my iPhone") == "Where is my key?"
    assert strip_signature("Waar is mijn sleutel?\nVerstuurd vanaf mijn iPhone") == "Waar is mijn sleutel?"
    text = "Is rent due on the 1st?\n\nThis email and any attachments are confidential and intended solely for the addressee."
    assert strip_signature(text) == "Is rent due on the 1st?"


def test_automated_mail_is_recognised():
    assert is_automated({"Auto-Submitted": "auto-replied"}, "a@b.nl", "Re: x")
    assert is_automated({}, "a@b.nl", "Out of Office: back Monday")
    assert is_automated({}, "a@b.nl", "Automatisch antwoord: afwezig")
    assert is_automated({"List-Unsubscribe": "<mailto:x>"}, "news@b.nl", "Weekly news")
    assert is_automated({}, "mailer-daemon@b.nl", "Delivery Status Notification (Failure)")
    assert is_automated({}, "priya@example.com", "Deposit question") is None


def test_name_sources_in_order_and_no_guessing():
    assert customer_name("Where are my keys?\n\nKind regards, Mateo Rossi") == "Mateo"
    assert customer_name("Where are my keys?", "Aiko Tanaka", "x@y.jp") == "Aiko"
    assert customer_name("Where are my keys?", "", "lena.berg@uni.example") == "Lena"
    assert customer_name("Where are my keys?", "", "info@company.example") is None
    assert customer_name("Where are my keys?", "Student Services", "x@y") is None


def test_language():
    assert language("Hoi, ik kom zaterdag aan. Hoe krijg ik mijn sleutel?") == "nl"
    assert language("Hallo, ich komme am Samstag an. Wie bekomme ich meinen Schlüssel?") == "de"
    assert language("Hi, when do I get my deposit back?") == "en"


STYLE = {"en": {"greeting_named": "Hi {name},", "greeting": "Hi,", "signoff": "Kind regards,\nThe Harbourbrook team"}}


def test_format_reply_lays_out_an_email():
    body = ("Hi [[name]], thanks for your message. After move-out our caretaker inspects the room within 5 working days. "
            "Your deposit is paid back within 30 days. If there are deductions you'll get a statement. "
            "Kind regards, the Harbourbrook team")
    out = format_reply(body, STYLE, "Priya")
    assert out.startswith("Hi Priya,\n\nThanks for your message.")
    assert out.endswith("\n\nKind regards,\nThe Harbourbrook team")
    assert out.count("Kind regards") == 1 and "[[name]]" not in out


def test_format_reply_without_a_name_says_hi():
    out = format_reply("Hi [[name]], the office opens at 10:00.", STYLE, None)
    assert out.startswith("Hi,\n\nThe office opens at 10:00.")


def test_no_style_leaves_text_alone():
    assert format_reply("We're here to help!", None, "Priya") == "We're here to help!"


def test_a_closing_sentence_is_not_mistaken_for_a_signoff():
    out = format_reply("The caretaker will visit today. Thanks for your patience.", STYLE, "Priya")
    assert "Thanks for your patience." in out


def test_dutch_signoff_is_replaced_with_the_firms():
    style = {"nl": {"greeting_named": "Hoi {name},", "greeting": "Hoi,", "signoff": "Groet,\nHet Harbourbrook-team"}}
    out = format_reply("Hoi [[name]], je kunt de sleutelkluis gebruiken. Groet, het Harbourbrook-team", style, "Sanne", "nl")
    assert out == "Hoi Sanne,\n\nJe kunt de sleutelkluis gebruiken.\n\nGroet,\nHet Harbourbrook-team"

from inbox.textclean import clean, is_deflection, placeholders, redact


def test_clean_strips_handles_urls_and_signoffs():
    raw = "@115712 @AmericanAir Sorry &amp; thanks. See https://t.co/abc ^MS"
    assert clean(raw) == "Sorry & thanks. See [link]"


def test_deflection_catches_dm_and_links():
    assert is_deflection("Please DM us your confirmation code.")
    assert is_deflection("Contact our support team here: [link]")
    assert is_deflection("If you give our AAdvantage Customer Service team a call, they'll help.")
    assert not is_deflection("There shouldn't be a charge to apply the credit.")
    # A link to the answer is still an answer.
    assert not is_deflection("Bags can be up to 50 pounds each. [link]")


def test_redact_name_after_yes():
    assert redact("Yes, Leah, Basic Economy is more restrictive.") == (
        "Yes, [[name]], Basic Economy is more restrictive."
    )


def test_redact_customer_details():
    text = "Hey Lindie! Your order 405-6299213-5413144 on flight 1329, code XK4J2P, call 312-555-0199."
    out = redact(text)
    assert "Lindie" not in out
    assert "405-6299213" not in out
    assert "1329" not in out
    assert "XK4J2P" not in out
    assert "312-555-0199" not in out
    assert placeholders(out) == [
        "[[name]]", "[[order number]]", "[[flight number]]", "[[booking reference]]", "[[phone]]",
    ]


def test_redact_keeps_the_firms_own_number():
    out = redact("Give our Reservations team a call at 800-433-7300.", ["800-433-7300"])
    assert "800-433-7300" in out


def test_redact_vocative_name_but_not_common_words():
    assert "[[name]]" in redact("It's always a pleasure to have you on board, John.")
    assert redact("We'll see you soon, Friday.") == "We'll see you soon, Friday."
    assert redact("Thanks, Please.") == "Thanks, Please."


def test_redact_leaves_plain_words_alone():
    text = "Which iOS version is your device running right now?"
    assert redact(text) == text


def test_redact_accented_and_non_english_greetings():
    assert "Tomás" not in redact("Hi Tomás, we understand.")
    assert redact("Hoi Daan, na je vertrek.") == "Hoi [[name]], na je vertrek."
    assert redact("Hallo Lena, nach dem Auszug.") == "Hallo [[name]], nach dem Auszug."


def test_answers_keep_their_links_and_links_survive_redaction():
    raw = "Here's the workaround until it's fixed: https://t.co/XK4J2PAB12 and https://t.co/XK4J2P"
    out = redact(clean(raw, keep_urls=True))
    assert "https://t.co/XK4J2PAB12" in out and "https://t.co/XK4J2P" in out
    assert clean(raw) == "Here's the workaround until it's fixed: [link] and [link]"
